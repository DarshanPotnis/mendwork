"""A ModelPort over HTTP: the behaviour every hosted or local provider shares.

A provider differs only in its wire format (where to post, what to send, how to read the
reply). Everything else is common: the circuit breaker is checked first, the transport bounds
time and retries, usage and estimated cost are measured around the whole call, and the reply
text is parsed by the engine's one strict parser.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import JsonValue

from mendwork.adapters.models.breaker import CircuitBreaker
from mendwork.adapters.models.pricing import ModelPrice, estimated_cost
from mendwork.adapters.models.replies import choice_result
from mendwork.adapters.models.transport import ModelHttpClient
from mendwork.engine.domain.model_evidence import ModelUsage
from mendwork.engine.errors import ProviderError
from mendwork.engine.healing.choice import ChoiceProblem
from mendwork.engine.ports.model_types import ChoiceRequest, ChoiceResult
from mendwork.engine.ports.timer import Timer


class Completion(StrEnum):
    """How a provider says its reply ended."""

    COMPLETE = "complete"
    CUT_OFF = "cut_off"
    """The reply hit the output token limit."""
    WITHHELD = "withheld"
    """The provider refused or filtered the reply."""


@dataclass(frozen=True, slots=True)
class DecodedReply:
    """The text of a provider's reply, how it ended, and the tokens it reported."""

    text: str
    completion: Completion
    input_tokens: int | None
    output_tokens: int | None


class WireFormat(Protocol):
    """One provider's request and response shapes."""

    @property
    def provider(self) -> str:
        """The provider's name in evidence."""
        ...

    @property
    def local(self) -> bool:
        """Whether calls run on this machine and cost nothing."""
        ...

    def url(self) -> str:
        """Where a choice request is posted."""
        ...

    def headers(self) -> dict[str, str]:
        """Request headers, credentials included."""
        ...

    def encode(self, request: ChoiceRequest) -> dict[str, JsonValue]:
        """The request body."""
        ...

    def decode(self, body: bytes) -> DecodedReply:
        """The reply, or ProviderError(reason=malformed_response) when it is not the shape
        the provider documents."""
        ...


class HttpChoiceModel:
    """A provider reached over HTTP."""

    def __init__(
        self,
        *,
        wire: WireFormat,
        model: str,
        transport: ModelHttpClient,
        breaker: CircuitBreaker,
        price: ModelPrice | None,
        timer: Timer,
    ) -> None:
        self._wire = wire
        self._model = model
        self._transport = transport
        self._breaker = breaker
        self._price = price
        self._timer = timer

    async def choose_candidate(self, request: ChoiceRequest) -> ChoiceResult:
        self._breaker.before_call()
        started = self._timer.monotonic()
        try:
            reply = await self._transport.post_json(
                self._wire.url(),
                self._wire.encode(request),
                headers=self._wire.headers(),
                timeout_ms=request.timeout_ms,
            )
        except ProviderError as error:
            self._breaker.failed()
            attempts = error.context.get("attempts")
            usage = self._usage(started, attempts if isinstance(attempts, int) else 0, None)
            raise ProviderError(error.message, **{**error.context, "usage": usage}) from error
        try:
            decoded = self._wire.decode(reply.body)
        except ProviderError as error:
            self._breaker.failed()
            usage = self._usage(started, reply.attempts, None)
            raise ProviderError(error.message, **{**error.context, "usage": usage}) from error
        self._breaker.succeeded()
        usage = self._usage(started, reply.attempts, decoded)
        return choice_result(decoded.text, usage, problem=_problem(decoded.completion))

    def _usage(self, started: float, attempts: int, decoded: DecodedReply | None) -> ModelUsage:
        input_tokens = decoded.input_tokens if decoded is not None else None
        output_tokens = decoded.output_tokens if decoded is not None else None
        return ModelUsage(
            provider=self._wire.provider,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=max(0, round((self._timer.monotonic() - started) * 1000)),
            http_attempts=attempts,
            estimated_cost_usd=estimated_cost(
                local=self._wire.local,
                price=self._price,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )


def json_object(body: bytes, provider: str) -> dict[str, JsonValue]:
    """A reply body as a JSON object, or ProviderError(reason=malformed_response)."""
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        document = None
    if not isinstance(document, dict):
        raise ProviderError(
            f"{provider} answered with something other than a JSON object",
            reason="malformed_response",
        )
    return document


def malformed(provider: str, what: str) -> ProviderError:
    """A reply that lacks a field the provider documents."""
    return ProviderError(f"{provider}'s reply has no {what}", reason="malformed_response")


def token_count(value: JsonValue) -> int | None:
    """A reported token count, or None when absent or not a count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _problem(completion: Completion) -> ChoiceProblem | None:
    match completion:
        case Completion.COMPLETE:
            return None
        case Completion.CUT_OFF:
            return ChoiceProblem.CUT_OFF
        case Completion.WITHHELD:
            return ChoiceProblem.WITHHELD
