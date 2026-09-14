"""Live calls to the model provider MENDWORK_MODEL_* configures: ``make live-providers`` only.

Each canonical request goes to the real provider, which must answer in the required shape within
the configured time; the choice, latency, and tokens are printed. These tests check the contract
with a real service, not the model's judgement, and never run in CI.
"""

import pytest

from mendwork.apps.cli.wiring import chat_model, model_client
from mendwork.engine.healing.prompt import PROMPT_VERSION, response_schema
from mendwork.engine.ports.model_types import ChoiceRequest
from mendwork.settings import Settings
from mendwork.settings_model import ModelProvider
from tests.unit.healing.prompt_cases import CANONICAL, render_case

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


@pytest.fixture
def configured() -> Settings:
    settings = Settings()
    if settings.model_provider is ModelProvider.NONE:
        pytest.fail(
            "make live-providers calls the configured provider: set MENDWORK_MODEL_PROVIDER and "
            "MENDWORK_MODEL_NAME (and MENDWORK_MODEL_API_KEY or MENDWORK_MODEL_BASE_URL if needed)"
        )
    return settings


@pytest.mark.parametrize("name", sorted(CANONICAL))
async def test_the_configured_provider_answers_a_canonical_request_in_the_required_shape(
    configured: Settings, name: str, request: pytest.FixtureRequest
) -> None:
    case = CANONICAL[name]
    choice_request = ChoiceRequest(
        prompt_version=PROMPT_VERSION,
        messages=render_case(case),
        response_schema=response_schema(len(case.shown)),
        shown=case.shown,
        timeout_ms=configured.model_timeout_ms,
    )

    async with model_client(configured) as client:
        assert client is not None
        model = chat_model(configured, client=client)
        assert model is not None
        result = await model.choose_candidate(choice_request)

    assert result.choice is None or 1 <= result.choice <= len(case.shown)
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if isinstance(reporter, pytest.TerminalReporter):
        usage = result.usage
        reporter.write_line(
            f"{name}: {usage.provider} {usage.model} chose {result.choice} "
            f"(confidence {result.confidence:.2f}) in {usage.latency_ms} ms, "
            f"{usage.input_tokens} tokens in, {usage.output_tokens} out: {result.reason}"
        )
