"""Gemini's ``models/{model}:generateContent`` with JSON-schema output.

The system prompt goes in ``systemInstruction``; chat turns become ``contents`` with the
roles ``user`` and ``model``. The reply is constrained with ``responseMimeType`` and
``responseJsonSchema``. A prompt blocked by safety filters, or a candidate that finished for
any reason but ``STOP`` or ``MAX_TOKENS``, is a withheld answer; thought parts are not answer
text, but their tokens are billed and counted as output.

Free-tier inputs may be used to improve Google's models, so this provider is for chaos-portal
and benchmark data only (ARCHITECTURE.md §10).
"""

from dataclasses import dataclass
from typing import Final

from pydantic import JsonValue, SecretStr

from mendwork.adapters.models.http_model import (
    Completion,
    DecodedReply,
    json_object,
    malformed,
    token_count,
)
from mendwork.engine.ports.model_types import ChatRole, ChoiceRequest

PROVIDER: Final = "gemini"
_ROLES: Final = {ChatRole.USER: "user", ChatRole.ASSISTANT: "model"}


@dataclass(frozen=True, slots=True)
class GeminiOptions:
    """Where the API is, which model, and how it is run."""

    base_url: str
    model: str
    api_key: SecretStr
    temperature: float
    seed: int
    max_output_tokens: int
    think: bool | None


class GeminiWire:
    """Gemini's generateContent request and response shapes."""

    def __init__(self, options: GeminiOptions) -> None:
        self._options = options

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def local(self) -> bool:
        return False

    def url(self) -> str:
        options = self._options
        return f"{options.base_url.rstrip('/')}/models/{options.model}:generateContent"

    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._options.api_key.get_secret_value()}

    def encode(self, request: ChoiceRequest) -> dict[str, JsonValue]:
        options = self._options
        system = [message.text for message in request.messages if message.role is ChatRole.SYSTEM]
        contents: list[JsonValue] = [
            {"role": _ROLES[message.role], "parts": [{"text": message.text}]}
            for message in request.messages
            if message.role is not ChatRole.SYSTEM
        ]
        generation: dict[str, JsonValue] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": request.response_schema,
            "temperature": options.temperature,
            "seed": options.seed,
            "maxOutputTokens": options.max_output_tokens,
        }
        if options.think is False:
            generation["thinkingConfig"] = {"thinkingBudget": 0}
        payload: dict[str, JsonValue] = {"contents": contents, "generationConfig": generation}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": text} for text in system]}
        return payload

    def decode(self, body: bytes) -> DecodedReply:
        document = json_object(body, PROVIDER)
        usage = document.get("usageMetadata")
        counts = usage if isinstance(usage, dict) else {}
        input_tokens = token_count(counts.get("promptTokenCount"))
        output_tokens = _output_tokens(counts)
        feedback = document.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason") is not None:
            return DecodedReply("", Completion.WITHHELD, input_tokens, output_tokens)
        candidates = document.get("candidates")
        first = candidates[0] if isinstance(candidates, list) and candidates else None
        if not isinstance(first, dict):
            raise malformed(PROVIDER, "candidate")
        finish = first.get("finishReason")
        content = first.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        texts: list[str] = []
        for part in parts if isinstance(parts, list) else []:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(part, dict) and isinstance(text, str) and not part.get("thought"):
                texts.append(text)
        if finish not in {"STOP", "MAX_TOKENS"}:
            return DecodedReply("".join(texts), Completion.WITHHELD, input_tokens, output_tokens)
        return DecodedReply(
            text="".join(texts),
            completion=Completion.CUT_OFF if finish == "MAX_TOKENS" else Completion.COMPLETE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def _output_tokens(counts: dict[str, JsonValue]) -> int | None:
    candidates = token_count(counts.get("candidatesTokenCount"))
    thoughts = token_count(counts.get("thoughtsTokenCount")) or 0
    return None if candidates is None else candidates + thoughts
