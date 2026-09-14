"""Ollama, a local model server: ``POST /api/chat`` with the reply constrained to a JSON Schema.

Calls run on this machine and cost nothing. Output is constrained with ``format`` (the reply
schema), made repeatable with a fixed temperature and seed, and bounded with ``num_predict``
and ``num_ctx``. ``think`` is sent only when configured, because models without a thinking
mode do not accept it.
"""

from dataclasses import dataclass

from pydantic import JsonValue

from mendwork.adapters.models.http_model import (
    Completion,
    DecodedReply,
    json_object,
    malformed,
    token_count,
)
from mendwork.engine.ports.model_types import ChoiceRequest

PROVIDER = "ollama"


@dataclass(frozen=True, slots=True)
class OllamaOptions:
    """Where the server is and how the model is run."""

    base_url: str
    model: str
    temperature: float
    seed: int
    max_output_tokens: int
    context_tokens: int
    keep_alive: str
    think: bool | None


class OllamaWire:
    """Ollama's chat request and response shapes."""

    def __init__(self, options: OllamaOptions) -> None:
        self._options = options

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def local(self) -> bool:
        return True

    def url(self) -> str:
        return f"{self._options.base_url.rstrip('/')}/api/chat"

    def headers(self) -> dict[str, str]:
        return {}

    def encode(self, request: ChoiceRequest) -> dict[str, JsonValue]:
        options = self._options
        payload: dict[str, JsonValue] = {
            "model": options.model,
            "messages": [
                {"role": message.role.value, "content": message.text}
                for message in request.messages
            ],
            "stream": False,
            "format": request.response_schema,
            "keep_alive": options.keep_alive,
            "options": {
                "temperature": options.temperature,
                "seed": options.seed,
                "num_predict": options.max_output_tokens,
                "num_ctx": options.context_tokens,
            },
        }
        if options.think is not None:
            payload["think"] = options.think
        return payload

    def decode(self, body: bytes) -> DecodedReply:
        document = json_object(body, PROVIDER)
        message = document.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise malformed(PROVIDER, "message content")
        return DecodedReply(
            text=content,
            completion=(
                Completion.CUT_OFF
                if document.get("done_reason") == "length"
                else Completion.COMPLETE
            ),
            input_tokens=token_count(document.get("prompt_eval_count")),
            output_tokens=token_count(document.get("eval_count")),
        )
