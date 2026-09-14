"""Any server that speaks the OpenAI Chat Completions API: ``POST {base_url}/chat/completions``.

The reply is constrained with ``response_format`` of type ``json_schema`` in strict mode. The
key, when configured, is sent as a bearer token; local servers usually need none. A
``refusal`` or a ``content_filter`` finish is a withheld answer, and ``length`` a reply cut
off at the token limit.
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
from mendwork.engine.ports.model_types import ChoiceRequest

PROVIDER: Final = "openai_compatible"
SCHEMA_NAME: Final = "candidate_choice"


@dataclass(frozen=True, slots=True)
class OpenAICompatibleOptions:
    """Where the server is, which model, and how it is run."""

    base_url: str
    model: str
    api_key: SecretStr | None
    temperature: float
    seed: int
    max_output_tokens: int
    local: bool
    """Whether the server runs on this machine, so calls cost nothing."""


class OpenAICompatibleWire:
    """The Chat Completions request and response shapes."""

    def __init__(self, options: OpenAICompatibleOptions) -> None:
        self._options = options

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def local(self) -> bool:
        return self._options.local

    def url(self) -> str:
        return f"{self._options.base_url.rstrip('/')}/chat/completions"

    def headers(self) -> dict[str, str]:
        key = self._options.api_key
        return {} if key is None else {"Authorization": f"Bearer {key.get_secret_value()}"}

    def encode(self, request: ChoiceRequest) -> dict[str, JsonValue]:
        options = self._options
        return {
            "model": options.model,
            "messages": [
                {"role": message.role.value, "content": message.text}
                for message in request.messages
            ],
            "temperature": options.temperature,
            "seed": options.seed,
            "max_tokens": options.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": SCHEMA_NAME,
                    "schema": request.response_schema,
                    "strict": True,
                },
            },
        }

    def decode(self, body: bytes) -> DecodedReply:
        document = json_object(body, PROVIDER)
        usage = document.get("usage")
        counts = usage if isinstance(usage, dict) else {}
        input_tokens = token_count(counts.get("prompt_tokens"))
        output_tokens = token_count(counts.get("completion_tokens"))
        choices = document.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        if not isinstance(first, dict) or not isinstance(message, dict):
            raise malformed(PROVIDER, "choice message")
        content = message.get("content")
        finish = first.get("finish_reason")
        if isinstance(message.get("refusal"), str) or finish == "content_filter":
            return DecodedReply("", Completion.WITHHELD, input_tokens, output_tokens)
        return DecodedReply(
            text=content if isinstance(content, str) else "",
            completion=Completion.CUT_OFF if finish == "length" else Completion.COMPLETE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
