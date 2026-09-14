"""The ModelPort: a model that picks one numbered candidate, or none.

This is the only thing the engine asks of a model. It never asks for a selector, never sends
markup, and treats the answer as a proposal that the safety rules and the step's checkpoints
still decide on.
"""

from typing import Protocol

from mendwork.engine.ports.model_types import ChoiceRequest, ChoiceResult


class ModelPort(Protocol):
    """A configured model provider."""

    async def choose_candidate(self, request: ChoiceRequest) -> ChoiceResult:
        """Ask which numbered candidate is the recorded control.

        Raises ModelOutputInvalid when the model replied in any other shape, and ProviderError
        when the provider could not be asked or did not answer within ``request.timeout_ms``.
        Either error carries the call's usage under ``usage`` when a request was sent.
        """
        ...
