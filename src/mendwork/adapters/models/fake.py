"""A scripted model for tests and benchmarks, held to the same parsing as every provider.

Replies come from a list, in order, or from a function of the request (a benchmark can answer
from ground truth that way without product code ever seeing it). A reply is raw text, parsed
exactly as a provider's reply is, or a ProviderError to raise. The fake makes no network call
and records every request it was sent.
"""

from collections.abc import Callable, Sequence
from decimal import Decimal

from mendwork.adapters.models.replies import choice_result
from mendwork.engine.domain.model_evidence import ModelUsage
from mendwork.engine.errors import ProviderError
from mendwork.engine.ports.model_types import ChoiceRequest, ChoiceResult

Reply = str | ProviderError
Responder = Callable[[ChoiceRequest], Reply]


class FakeModel:
    """A ModelPort whose replies are written in advance."""

    def __init__(
        self,
        replies: Sequence[Reply] | Responder,
        *,
        provider: str = "fake",
        model: str = "scripted",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._replies = list(replies) if not callable(replies) else None
        self._responder = replies if callable(replies) else None
        self._provider = provider
        self._model = model
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.requests: list[ChoiceRequest] = []

    async def choose_candidate(self, request: ChoiceRequest) -> ChoiceResult:
        self.requests.append(request)
        usage = ModelUsage(
            provider=self._provider,
            model=self._model,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            latency_ms=0,
            http_attempts=0,
            estimated_cost_usd=Decimal(0),
        )
        reply = self._reply(request)
        if isinstance(reply, ProviderError):
            raise ProviderError(reply.message, **{**reply.context, "usage": usage})
        return choice_result(reply, usage)

    def _reply(self, request: ChoiceRequest) -> Reply:
        if self._responder is not None:
            return self._responder(request)
        if not self._replies:
            return ProviderError("the scripted model has no reply left", reason="script_exhausted")
        return self._replies.pop(0)
