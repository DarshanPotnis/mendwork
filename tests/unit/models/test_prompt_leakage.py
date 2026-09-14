"""No secret reaches a provider: a run types a secret, the page then echoes it in every encoding
near the controls Rung 3 describes, and every request body each real adapter sends is searched
for every encoding, as are the run's record and events."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx
import pytest
import pytest_asyncio
import respx
from pydantic import JsonValue, SecretStr

from mendwork.adapters.models.gemini import GeminiOptions, GeminiWire
from mendwork.adapters.models.http_model import WireFormat
from mendwork.adapters.models.ollama import OllamaOptions, OllamaWire
from mendwork.adapters.models.openai_compatible import (
    OpenAICompatibleOptions,
    OpenAICompatibleWire,
)
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.runs import RunStatus
from mendwork.engine.healing.model_rung import ModelChoiceConfig, ModelRung
from mendwork.engine.ports.element_types import Box
from mendwork.engine.replay.replayer import Replayer
from mendwork.engine.safety.budgets import BudgetLimits
from tests.fakes.browser import FakeBrowser, FakeElement, FakeLauncher
from tests.fakes.clock import FakeClock
from tests.fakes.ledger import InMemoryUsageLedger
from tests.fakes.ports import (
    DictSecretResolver,
    InMemoryArtifactStore,
    RecordingEventSink,
    SequenceRandom,
    SequentialRunIds,
)
from tests.fakes.timer import FakeTimer
from tests.secret_search import encodings, leaks
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button, facts_of
from tests.unit.models.helpers import choice_json, model
from tests.unit.replay.builders import config, selector
from tests.workflows import version

pytestmark = pytest.mark.asyncio

SECRET: Final = "Tr0ub4dor&3-zq"
PLANTED: Final = " ".join(form.decode("ascii") for form in encodings(SECRET) if b"\x00" not in form)
FIXTURES: Final = Path(__file__).resolve().parents[2] / "fixtures" / "models"
PASSCODE_ID: Final = selector(strategy="test_id", value="vault-passcode")
PASSCODE: Final = Fingerprint.model_validate(
    {
        "tag": "input",
        "accessible_name": "Vault passcode",
        "label_text": "Vault passcode",
        "attributes": {"id": "passcode", "type": "password", "data_testid": "vault-passcode"},
        "structural_path": "main > form > input",
        "selectors": [PASSCODE_ID.model_dump()],
    }
)
EXPORT: Final = export_button(selectors=[EXPORT_TEST_ID.model_dump()])


def wires() -> list[tuple[WireFormat, str, dict[str, JsonValue]]]:
    ollama_reply: dict[str, JsonValue] = {
        "message": {"role": "assistant", "content": choice_json(1)},
        "done_reason": "stop",
        "prompt_eval_count": 500,
        "eval_count": 30,
    }
    return [
        (
            OllamaWire(
                OllamaOptions(
                    base_url="http://127.0.0.1:11434",
                    model="m",
                    temperature=0.0,
                    seed=0,
                    max_output_tokens=200,
                    context_tokens=4_096,
                    keep_alive="5m",
                    think=None,
                )
            ),
            "http://127.0.0.1:11434/api/chat",
            ollama_reply,
        ),
        (
            GeminiWire(
                GeminiOptions(
                    base_url="https://generativelanguage.googleapis.com/v1beta",
                    model="m",
                    api_key=SecretStr("k"),
                    temperature=0.0,
                    seed=0,
                    max_output_tokens=200,
                    think=None,
                )
            ),
            "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent",
            json.loads((FIXTURES / "gemini_generate_ok.json").read_text(encoding="utf-8")),
        ),
        (
            OpenAICompatibleWire(
                OpenAICompatibleOptions(
                    base_url="https://llm.example.test/v1",
                    model="m",
                    api_key=None,
                    temperature=0.0,
                    seed=0,
                    max_output_tokens=200,
                    local=False,
                )
            ),
            "https://llm.example.test/v1/chat/completions",
            json.loads((FIXTURES / "openai_chat_ok.json").read_text(encoding="utf-8")),
        ),
    ]


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as http:
        yield http


def page_echoing_the_secret() -> FakeBrowser:
    page = FakeBrowser(timer=FakeTimer())
    page.elements["passcode"] = FakeElement(
        tag="input", name="Vault passcode", input_type="password"
    )
    page.facts["passcode"] = facts_of(PASSCODE)
    page.finds[PASSCODE_ID] = "passcode"

    def exported(browser: FakeBrowser) -> None:
        browser.text = "Ledger exported"

    add_element(
        page,
        "export",
        EXPORT,
        identity={"name": f"Download for {SECRET}"},
        facts={
            "text": f"Download for {PLANTED}",
            "id": "download",
            "label_text": PLANTED,
            "nearby_text": ("Quarterly ledger", f"Signed in with {PLANTED}", SECRET),
            "box": Box(x=0.6, y=0.3, width=0.12, height=0.05),
        },
        on_action=exported,
    )
    return page


@pytest.mark.parametrize("index", [0, 1, 2], ids=["ollama", "gemini", "openai_compatible"])
async def test_no_encoding_of_a_typed_secret_reaches_any_provider(
    client: httpx.AsyncClient, index: int
) -> None:
    wire, url, provider_reply = wires()[index]
    page = page_echoing_the_secret()
    events = RecordingEventSink()
    artifacts = InMemoryArtifactStore()
    rung = ModelRung(
        model=model(wire, client, FakeTimer(), name="m"),
        config=ModelChoiceConfig(
            candidates_k=5, timeout_ms=5_000, provider=wire.provider, model="m"
        ),
        limits=BudgetLimits(per_run=4, per_day=200),
        ledger=InMemoryUsageLedger(),
    )
    replayer = Replayer(
        launcher=FakeLauncher(page),
        artifacts=artifacts,
        events=events,
        secrets=DictSecretResolver({"vault_passcode": SECRET}),
        clock=FakeClock(datetime(2026, 9, 13, tzinfo=UTC)),
        timer=page.timer,
        randomness=SequenceRandom([0.0]),
        run_ids=SequentialRunIds(),
        config=config(),
        model=rung,
    )
    workflow = version(
        steps=[
            {
                "id": "unlock",
                "intent": "Fill the 'Vault passcode' field",
                "action": "fill",
                "risk": "caution",
                "target": PASSCODE.model_dump(mode="json"),
                "value": {"kind": "secret", "name": "vault_passcode"},
                "checkpoints": [{"kind": "field_has_value"}],
            },
            {
                "id": "export",
                "intent": "Click the 'Export ledger' button",
                "action": "click",
                "risk": "safe",
                "target": EXPORT.model_dump(mode="json"),
                "checkpoints": [{"kind": "text_present", "text": "Ledger exported"}],
            },
        ],
        secrets=["vault_passcode"],
    )

    with respx.mock() as router:
        route = router.post(url).mock(return_value=httpx.Response(200, json=provider_reply))
        run = await replayer.run(workflow, {})

    assert run.status is RunStatus.SUCCEEDED, run.error
    assert route.call_count == 1
    for call in route.calls:
        assert leaks(call.request.content, SECRET) == []
        assert "Download for" in call.request.content.decode("utf-8")
    record = artifacts.files[(run.run_id, next(n for r, n in artifacts.files if n == "run.json"))]
    assert leaks(record, SECRET) == []
    for event in events.events:
        assert leaks(event.model_dump_json().encode("utf-8"), SECRET) == []
