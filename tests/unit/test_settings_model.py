"""Model provider settings: off by default, complete when on, and wired into the right adapter."""

from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mendwork.adapters.models.gemini import GeminiWire
from mendwork.adapters.models.http_model import HttpChoiceModel
from mendwork.adapters.models.ollama import OllamaWire
from mendwork.adapters.models.openai_compatible import OpenAICompatibleWire
from mendwork.adapters.usage_fs.ledger import FileUsageLedger
from mendwork.apps.cli.wiring import (
    budget_limits,
    chat_model,
    model_choice_config,
    model_client,
    model_rung,
    wire_format,
)
from mendwork.engine.healing.model_rung import ModelChoiceConfig
from mendwork.engine.safety.budgets import BudgetLimits
from mendwork.settings import Settings
from mendwork.settings_model import DEFAULT_MODEL_BASE_URLS, ModelProvider


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]  # values are field names


def test_no_model_is_configured_by_default() -> None:
    defaults = settings()

    assert defaults.model_provider is ModelProvider.NONE
    assert defaults.model_endpoint() is None
    assert (defaults.model_max_calls_per_run, defaults.model_max_calls_per_day) == (4, 200)


@pytest.mark.parametrize(
    ("values", "needs"),
    [
        (
            {"model_provider": "ollama"},
            "MENDWORK_MODEL_PROVIDER=ollama also needs MENDWORK_MODEL_NAME",
        ),
        (
            {"model_provider": "gemini", "model_name": "flash"},
            "MENDWORK_MODEL_PROVIDER=gemini also needs MENDWORK_MODEL_API_KEY",
        ),
        (
            {"model_provider": "openai_compatible", "model_name": "chooser"},
            "MENDWORK_MODEL_PROVIDER=openai_compatible also needs MENDWORK_MODEL_BASE_URL",
        ),
    ],
)
def test_a_configured_provider_must_be_complete(values: dict[str, object], needs: str) -> None:
    with pytest.raises(ValidationError, match=needs):
        settings(**values)


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("ftp://models.example.test", "absolute http or https URL"),
        ("http://user:hunter2@models.example.test", "must not embed credentials"),
        ("https://models.example.test/v1?key=abc", "query string or fragment"),
    ],
)
def test_a_base_url_must_be_a_plain_http_url(url: str, problem: str) -> None:
    with pytest.raises(ValidationError, match=problem) as caught:
        settings(model_base_url=url, model_api_key="sk-not-a-real-key")

    assert "sk-not-a-real-key" not in str(caught.value)


def test_retry_pauses_must_be_ordered_and_keep_alive_must_be_a_duration() -> None:
    with pytest.raises(ValidationError, match="model_retry_max_delay_ms"):
        settings(model_retry_initial_delay_ms=5_000, model_retry_max_delay_ms=1_000)
    with pytest.raises(ValidationError, match="model_keep_alive"):
        settings(model_keep_alive="five minutes")


def test_prices_are_read_from_json_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "MENDWORK_MODEL_PRICES",
        '{"flash": {"input_usd_per_million_tokens": "0.10", '
        '"output_usd_per_million_tokens": "0.40"}}',
    )

    price = settings().model_prices["flash"]

    assert (price.input_usd_per_million_tokens, price.output_usd_per_million_tokens) == (
        Decimal("0.10"),
        Decimal("0.40"),
    )


def test_each_provider_has_its_default_endpoint_unless_one_is_configured() -> None:
    assert (
        settings(model_provider="ollama", model_name="m").model_endpoint()
        == (DEFAULT_MODEL_BASE_URLS[ModelProvider.OLLAMA])
    )
    assert (
        settings(
            model_provider="ollama", model_name="m", model_base_url="http://10.1.2.3:11434"
        ).model_endpoint()
        == "http://10.1.2.3:11434"
    )


@pytest.mark.parametrize(
    ("values", "wire"),
    [
        ({"model_provider": "ollama", "model_name": "m"}, OllamaWire),
        ({"model_provider": "gemini", "model_name": "m", "model_api_key": "k"}, GeminiWire),
        (
            {
                "model_provider": "openai_compatible",
                "model_name": "m",
                "model_base_url": "http://127.0.0.1:8080/v1",
            },
            OpenAICompatibleWire,
        ),
    ],
)
def test_the_configured_provider_is_wired_to_its_wire_format(
    values: dict[str, object], wire: type[object]
) -> None:
    assert isinstance(wire_format(settings(**values), "m"), wire)


@pytest.mark.asyncio
async def test_no_client_and_no_model_are_built_when_no_provider_is_configured(
    tmp_path: Path,
) -> None:
    async with model_client(settings()) as client:
        assert client is None
    async with httpx.AsyncClient() as http:
        assert chat_model(settings(), client=http) is None
        assert model_rung(settings(), client=http, ledger_directory=tmp_path) is None


@pytest.mark.asyncio
async def test_a_configured_model_rung_takes_its_limits_and_ledger_from_settings(
    tmp_path: Path,
) -> None:
    configured = settings(
        model_provider="ollama",
        model_name="local-chooser:4b",
        model_candidates_k=3,
        model_timeout_ms=15_000,
        model_max_calls_per_run=2,
        model_max_calls_per_day=50,
    )

    async with model_client(configured) as client:
        assert isinstance(client, httpx.AsyncClient)
        rung = model_rung(configured, client=client, ledger_directory=tmp_path / "usage")

    assert rung is not None
    assert isinstance(rung.model, HttpChoiceModel)
    assert rung.config == ModelChoiceConfig(
        candidates_k=3, timeout_ms=15_000, provider="ollama", model="local-chooser:4b"
    )
    assert rung.limits == BudgetLimits(per_run=2, per_day=50)
    assert isinstance(rung.ledger, FileUsageLedger)
    assert rung.ledger.directory == tmp_path / "usage"
    assert model_choice_config(configured) == rung.config
    assert budget_limits(configured) == rung.limits
