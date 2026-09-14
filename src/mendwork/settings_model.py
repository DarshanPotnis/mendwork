"""Model provider settings: Rung 3's model, its limits, and its budgets (ADR 0010).

A base of ``Settings``, kept in its own module so neither file grows unreadable; every field is
still a ``MENDWORK_`` variable of the one Settings object. No model is configured by default:
sending page descriptions to any model, even a local one, is a choice a person makes.
"""

from enum import StrEnum
from typing import Final, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings

from mendwork.adapters.models.pricing import ModelPrice


class ModelProvider(StrEnum):
    """Which provider Rung 3 asks, if any."""

    NONE = "none"
    OLLAMA = "ollama"
    GEMINI = "gemini"
    OPENAI_COMPATIBLE = "openai_compatible"


DEFAULT_MODEL_BASE_URLS: Final = {
    ModelProvider.OLLAMA: "http://127.0.0.1:11434",
    ModelProvider.GEMINI: "https://generativelanguage.googleapis.com/v1beta",
}
_MAX_TIMEOUT_MS: Final = 600_000


class ModelSettings(BaseSettings):
    """Rung 3's model provider, limits, and budgets."""

    model_provider: ModelProvider = ModelProvider.NONE
    # The model's name as the provider knows it, such as an Ollama tag. Names change monthly,
    # so there is no default.
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    # Defaults per provider in DEFAULT_MODEL_BASE_URLS; required for openai_compatible.
    model_base_url: str | None = None
    model_api_key: SecretStr | None = None
    # One call, retries included, and always capped by the heal deadline. Measured on an 8 GB Apple
    # M2 with Ollama and a 4B model (ADR 0010): warm calls 2.9-3.5 s, cold calls 7.5-22.6 s. The
    # heal budget (30 s) is the binding limit, so this equals it.
    model_timeout_ms: int = Field(default=30_000, ge=1, le=_MAX_TIMEOUT_MS)
    # How many eligible candidates, best first, a model is shown. Latency is flat from 1 to 8
    # (330 to 553 prompt tokens); the portal never had more than 3 eligible (ADR 0010).
    model_candidates_k: int = Field(default=5, ge=1, le=20)
    # HTTP requests per call, the first included, for timeouts, dropped connections, 429, and 5xx.
    model_max_attempts: int = Field(default=3, ge=1, le=10)
    model_retry_initial_delay_ms: int = Field(default=500, ge=0, le=60_000)
    model_retry_max_delay_ms: int = Field(default=4_000, ge=0, le=300_000)
    # Consecutive failed calls that pause a provider, and for how long.
    model_breaker_failures: int = Field(default=3, ge=1, le=100)
    model_breaker_reset_ms: int = Field(default=60_000, ge=1, le=3_600_000)
    # Budgets are policy, not capability: a run that needs more model choices than this is
    # better re-recorded. 0 turns the model off without unconfiguring it.
    model_max_calls_per_run: int = Field(default=4, ge=0, le=1_000)
    model_max_calls_per_day: int = Field(default=200, ge=0, le=1_000_000)
    # Hosted models' prices per million tokens, keyed by model name. A local model is free; a
    # hosted model missing here is recorded as unpriced, never as free.
    model_prices: dict[str, ModelPrice] = Field(default_factory=dict)
    # For openai_compatible: whether the server runs on this machine, so calls cost nothing.
    model_local: bool = False
    model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    model_seed: int = Field(default=0, ge=0, le=2**31 - 1)
    # A reply is one small JSON object; this bounds a model that rambles.
    model_max_output_tokens: int = Field(default=200, ge=16, le=4_096)
    # Ollama's context window for a choice request (num_ctx), measured on the prompts (ADR 0010).
    model_context_tokens: int = Field(default=4_096, ge=512, le=262_144)
    # How long Ollama keeps the model loaded after a call.
    model_keep_alive: str = Field(default="5m", pattern=r"^(?:-1|0|[1-9][0-9]*(?:ms|s|m|h)?)$")
    # Whether a thinking model thinks first; unset sends nothing, for models without the mode.
    model_think: bool | None = None
    model_max_response_bytes: int = Field(default=65_536, ge=1_024, le=16 * 1024 * 1024)

    def model_endpoint(self) -> str | None:
        """The provider's base URL: the configured one, or the provider's default."""
        return self.model_base_url or DEFAULT_MODEL_BASE_URLS.get(self.model_provider)

    @model_validator(mode="after")
    def _model_provider_is_complete(self) -> Self:
        provider = self.model_provider
        if self.model_base_url is not None:
            _check_base_url(self.model_base_url)
        if self.model_retry_max_delay_ms < self.model_retry_initial_delay_ms:
            raise ValueError(
                "model_retry_max_delay_ms must not be less than model_retry_initial_delay_ms"
            )
        if provider is ModelProvider.NONE:
            return self
        missing: list[str] = []
        if self.model_name is None:
            missing.append("MENDWORK_MODEL_NAME")
        if provider is ModelProvider.GEMINI and self.model_api_key is None:
            missing.append("MENDWORK_MODEL_API_KEY")
        if provider is ModelProvider.OPENAI_COMPATIBLE and self.model_base_url is None:
            missing.append("MENDWORK_MODEL_BASE_URL")
        if missing:
            raise ValueError(
                f"MENDWORK_MODEL_PROVIDER={provider.value} also needs {', '.join(missing)}"
            )
        return self


def _check_base_url(value: str) -> None:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("model_base_url must be an absolute http or https URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("model_base_url must not embed credentials; use MENDWORK_MODEL_API_KEY")
    if parts.query or parts.fragment:
        raise ValueError("model_base_url must not have a query string or fragment")
