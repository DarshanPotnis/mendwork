"""Shared pieces for model adapter tests: a transport on a fake timer, and a canonical request."""

import json
from typing import Final

import httpx

from mendwork.adapters.models.breaker import CircuitBreaker
from mendwork.adapters.models.http_model import HttpChoiceModel, WireFormat
from mendwork.adapters.models.pricing import ModelPrice
from mendwork.adapters.models.transport import ModelHttpClient, TransportPolicy
from mendwork.engine.healing.prompt import PROMPT_VERSION, response_schema
from mendwork.engine.ports.model_types import ChoiceRequest
from mendwork.engine.replay.config import RetryPolicy
from tests.fakes.ports import SequenceRandom
from tests.fakes.timer import FakeTimer
from tests.unit.healing.prompt_cases import CANONICAL, render_case

RETRY: Final = RetryPolicy(
    max_attempts=3, initial_delay_ms=500, max_delay_ms=4_000, multiplier=2.0, jitter_ratio=0.5
)
POLICY: Final = TransportPolicy(max_attempts=3, retry=RETRY, max_response_bytes=4_096)
CASE: Final = CANONICAL["click_three_candidates"]
REQUEST: Final = ChoiceRequest(
    prompt_version=PROMPT_VERSION,
    messages=render_case(CASE),
    response_schema=response_schema(len(CASE.shown)),
    shown=CASE.shown,
    timeout_ms=20_000,
)


def transport(
    client: httpx.AsyncClient, timer: FakeTimer, *, max_bytes: int = 4_096
) -> ModelHttpClient:
    policy = TransportPolicy(max_attempts=3, retry=RETRY, max_response_bytes=max_bytes)
    return ModelHttpClient(
        client=client, policy=policy, timer=timer, randomness=SequenceRandom([0.0])
    )


def model(
    wire: WireFormat,
    client: httpx.AsyncClient,
    timer: FakeTimer,
    *,
    name: str,
    price: ModelPrice | None = None,
) -> HttpChoiceModel:
    return HttpChoiceModel(
        wire=wire,
        model=name,
        transport=transport(client, timer),
        breaker=CircuitBreaker(failure_threshold=3, reset_ms=60_000, timer=timer),
        price=price,
        timer=timer,
    )


def choice_json(choice: int | None = 1) -> str:
    return json.dumps({"choice": choice, "confidence": 0.86, "reason": "Same control, renamed."})
