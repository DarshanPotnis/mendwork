"""Measure the configured Ollama model on Rung 3 prompts: cold and warm latency by list length.

A cold call starts with the model unloaded (``keep_alive: 0``), as the first Rung 3 call of a run
usually does; warm calls follow while it is loaded. Every call is the real request Rung 3 sends,
with made-up candidates, through the product's own adapter. The script also reports the
model's resident size from ``/api/ps``. It is how ADR 0010 sets ``MENDWORK_MODEL_TIMEOUT_MS`` and
``MENDWORK_MODEL_CANDIDATES_K``.

    MENDWORK_MODEL_PROVIDER=ollama MENDWORK_MODEL_NAME=qwen3:4b-instruct-2507-q4_K_M \\
        uv run python -m benchmarks.chaos.model_latency --sizes 1 3 5 8 --warm 5
"""

import argparse
import asyncio
import math
import sys
from collections.abc import Sequence
from typing import Final, TextIO

import httpx
from pydantic import TypeAdapter

from mendwork.apps.cli.wiring import chat_model, model_client
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.model_evidence import CandidateDescription, ShownCandidate
from mendwork.engine.domain.steps import Step
from mendwork.engine.errors import ProviderError
from mendwork.engine.healing.prompt import PROMPT_VERSION, render_messages, response_schema
from mendwork.engine.ports.model_types import ChoiceRequest
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings
from mendwork.settings_model import ModelProvider

MEASUREMENT_TIMEOUT_MS: Final = 180_000
NAMES: Final = (
    ("Download ledger", "Quarterly ledger"),
    ("Export invoices", "Invoices"),
    ("Ledger", None),
    ("Quarterly summary", "Quarterly ledger"),
    ("Print ledger", "Quarterly ledger"),
    ("Ledger history", "History"),
    ("Export report", "Reports"),
    ("Ledger settings", "Settings"),
)
RECORDED: Final = Fingerprint.model_validate(
    {
        "tag": "button",
        "role": "button",
        "accessible_name": "Export ledger",
        "text": "Export ledger",
        "nearby_text": ["Quarterly ledger"],
        "structural_path": "main > article > div > button",
        "selectors": [{"strategy": "test_id", "value": "ledger-export"}],
    }
)
STEP: Final[Step] = TypeAdapter(Step).validate_python(
    {
        "id": "export",
        "intent": "Click the 'Export ledger' button",
        "action": "click",
        "risk": "safe",
        "target": RECORDED.model_dump(mode="json"),
        "checkpoints": [{"kind": "text_present", "text": "Ledger exported"}],
    }
)


def request_with(size: int) -> ChoiceRequest:
    """The request Rung 3 sends for a list of ``size`` candidates."""
    shown = tuple(
        ShownCandidate(
            number=number,
            candidate=f"c{number}",
            description=CandidateDescription(
                kind="button", name=name, nearby_text=() if nearby is None else (nearby,)
            ),
            similarity=round(0.58 - 0.04 * number, 2),
        )
        for number, (name, nearby) in enumerate(NAMES[:size], start=1)
    )
    return ChoiceRequest(
        prompt_version=PROMPT_VERSION,
        messages=render_messages(STEP, RECORDED, shown, SecretScrubber()),
        response_schema=response_schema(size),
        shown=shown,
        timeout_ms=MEASUREMENT_TIMEOUT_MS,
    )


def percentile(values: Sequence[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


async def measure(sizes: Sequence[int], warm: int, out: TextIO) -> int:
    settings = Settings().model_copy(update={"model_timeout_ms": MEASUREMENT_TIMEOUT_MS})
    base = settings.model_endpoint()
    name = settings.model_name
    if settings.model_provider is not ModelProvider.OLLAMA or base is None or name is None:
        out.write("set MENDWORK_MODEL_PROVIDER=ollama and MENDWORK_MODEL_NAME\n")
        return 2
    async with model_client(settings) as client:
        model = chat_model(settings, client=client) if client is not None else None
        if client is None or model is None:
            out.write("no model is configured\n")
            return 2
        out.write(f"model {name} · context {settings.model_context_tokens} tokens\n")
        out.write(
            "size  prompt tokens  cold ms  warm p50 ms  warm p95 ms  output tokens  choices\n"
        )
        for size in sizes:
            request = request_with(size)
            await _unload(client, base, name)
            try:
                cold = await model.choose_candidate(request)
                warms = [await model.choose_candidate(request) for _ in range(warm)]
            except ProviderError as error:
                out.write(f"{size}: {error.message}\n")
                return 1
            latencies = [result.usage.latency_ms for result in warms]
            outputs = [result.usage.output_tokens or 0 for result in (cold, *warms)]
            choices = sorted({result.choice for result in (cold, *warms)}, key=str)
            out.write(
                f"{size:>4}  {cold.usage.input_tokens:>13}  {cold.usage.latency_ms:>7}  "
                f"{percentile(latencies, 0.5):>11}  {percentile(latencies, 0.95):>11}  "
                f"{min(outputs)}-{max(outputs):<11}  {choices}\n"
            )
            out.flush()
        out.write(f"resident: {await _resident(client, base, name)}\n")
    return 0


async def _unload(client: httpx.AsyncClient, base: str, name: str) -> None:
    response = await client.post(
        f"{base}/api/generate", json={"model": name, "keep_alive": 0}, timeout=60
    )
    response.raise_for_status()


async def _resident(client: httpx.AsyncClient, base: str, name: str) -> str:
    response = await client.get(f"{base}/api/ps", timeout=10)
    response.raise_for_status()
    for loaded in response.json().get("models", []):
        if loaded.get("name") == name:
            return (
                f"{loaded.get('size', 0) / 2**30:.2f} GiB total, "
                f"{loaded.get('size_vram', 0) / 2**30:.2f} GiB on the GPU"
            )
    return "not loaded"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.chaos.model_latency", description=__doc__
    )
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 3, 5, 8])
    parser.add_argument("--warm", type=int, default=5)
    arguments = parser.parse_args(argv)
    return asyncio.run(measure(arguments.sizes, arguments.warm, sys.stdout))


if __name__ == "__main__":
    raise SystemExit(main())
