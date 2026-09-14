"""Values exchanged with a model: the numbered choice Rung 3 asks for, and the answer.

A request carries rendered chat messages, the JSON Schema a reply must follow, the structured
list of candidates the messages number, and a time limit. It never carries page markup, a
screenshot, a selector, or a value a step types, so a provider cannot receive any of them.
"""

from enum import StrEnum

from pydantic import Field, JsonValue

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.model_evidence import ModelUsage, ShownCandidate


class ChatRole(StrEnum):
    """Who a chat message is from."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(DomainModel):
    """One message of a chat request."""

    role: ChatRole
    text: str


class ChoiceRequest(DomainModel):
    """Which numbered candidate is the recorded control, asked within a time limit."""

    prompt_version: str
    messages: tuple[ChatMessage, ...] = Field(min_length=2)
    response_schema: dict[str, JsonValue]
    """The JSON Schema a reply must follow, for providers that constrain their output."""
    shown: tuple[ShownCandidate, ...] = Field(min_length=1)
    """The candidates the messages number, as structured data."""
    timeout_ms: int = Field(ge=1)
    """The whole call, retries included, must finish within this."""


class ChoiceResult(DomainModel):
    """A reply in the required shape. The choice is not yet checked against the list."""

    choice: int | None
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reason: str
    usage: ModelUsage
