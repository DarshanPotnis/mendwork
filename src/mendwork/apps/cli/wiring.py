"""Composition: Settings and adapters assembled into a Replayer.

Shared by ``mendwork run`` and the benchmark scripts, so both replay exactly the same way.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

import httpx

from mendwork.adapters.artifacts_local.records import LocalRunRecords
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import (
    EgressEnforcement,
    LaunchOptions,
    SessionOptions,
)
from mendwork.adapters.browser_playwright.recording.launcher import RecordingOptions
from mendwork.adapters.models.breaker import CircuitBreaker
from mendwork.adapters.models.gemini import GeminiOptions, GeminiWire
from mendwork.adapters.models.http_model import HttpChoiceModel, WireFormat
from mendwork.adapters.models.ollama import OllamaOptions, OllamaWire
from mendwork.adapters.models.openai_compatible import (
    OpenAICompatibleOptions,
    OpenAICompatibleWire,
)
from mendwork.adapters.models.transport import ModelHttpClient, TransportPolicy
from mendwork.adapters.secrets_env.resolver import EnvSecretResolver
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.system.randomness import SystemRandomSource
from mendwork.adapters.system.run_ids import TimestampRunIds
from mendwork.adapters.system.timer import AsyncioTimer
from mendwork.adapters.usage_fs.ledger import FileUsageLedger
from mendwork.engine.errors import MendworkError
from mendwork.engine.healing.config import FeatureWeights, HealingConfig
from mendwork.engine.healing.model_rung import ModelChoiceConfig, ModelRung
from mendwork.engine.ports.browser import BrowserLauncher
from mendwork.engine.ports.events import EventSink
from mendwork.engine.ports.model import ModelPort
from mendwork.engine.ports.resolver import HostResolver
from mendwork.engine.recording.config import RecordingConfig
from mendwork.engine.replay.config import ReplayConfig, RetryPolicy
from mendwork.engine.replay.replayer import Replayer
from mendwork.engine.safety.budgets import BudgetLimits
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.risk import RiskVocabulary
from mendwork.engine.safety.secret_registry import RegisteringSecretResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings
from mendwork.settings_model import ModelProvider

USAGE_DIRECTORY: Final = "usage"
"""The daily model-call ledger's directory, inside the artifacts directory."""


def replay_config(settings: Settings) -> ReplayConfig:
    """The engine's replay configuration, taken from Settings."""
    return ReplayConfig(
        step_timeout_ms=settings.step_timeout_ms,
        checkpoint_timeout_ms=settings.checkpoint_timeout_ms,
        navigation_timeout_ms=settings.navigation_timeout_ms,
        run_timeout_ms=settings.run_timeout_ms,
        settle_timeout_ms=settings.settle_timeout_ms,
        settle_quiet_frames=settings.settle_quiet_frames,
        retry=RetryPolicy(
            max_attempts=settings.navigation_max_attempts,
            initial_delay_ms=settings.retry_initial_delay_ms,
            max_delay_ms=settings.retry_max_delay_ms,
            multiplier=settings.retry_backoff_multiplier,
            jitter_ratio=settings.retry_jitter_ratio,
        ),
        healing=healing_config(settings),
    )


def healing_config(settings: Settings) -> HealingConfig:
    """The heal ladder's configuration, taken from Settings, with the risk vocabulary."""
    return HealingConfig(
        weights=FeatureWeights.model_validate(
            {feature.value: weight for feature, weight in settings.heal_weights().items()}
        ),
        accept_threshold=settings.heal_accept_threshold,
        accept_margin=settings.heal_accept_margin,
        name_similarity_floor=settings.heal_name_similarity_floor,
        position_scale=settings.heal_position_scale,
        candidates_max=settings.heal_candidates_max,
        max_attempts=settings.heal_max_attempts,
        authentication_max_attempts=settings.heal_authentication_max_attempts,
        report_candidates=settings.heal_report_candidates,
        timeout_ms=settings.heal_timeout_ms,
        vocabulary=risk_vocabulary(settings),
    )


def session_options(settings: Settings) -> SessionOptions:
    """How each run's browser context is set up."""
    return SessionOptions(
        viewport_width=settings.viewport_width,
        viewport_height=settings.viewport_height,
        default_timeout_ms=settings.step_timeout_ms,
        trace_on_failure=settings.trace_on_failure,
    )


def launch_options(settings: Settings, *, headed: bool, slow_mo_ms: int | None) -> LaunchOptions:
    """How Chromium is launched; command-line flags override Settings."""
    return LaunchOptions(
        headless=settings.browser_headless and not headed,
        slow_mo_ms=settings.browser_slow_mo_ms if slow_mo_ms is None else slow_mo_ms,
    )


def risk_vocabulary(settings: Settings) -> RiskVocabulary:
    """The risk classification vocabulary, taken from Settings."""
    return RiskVocabulary(
        danger_words=settings.risk_danger_words,
        soft_verbs=settings.risk_soft_verbs,
        view_state_nouns=settings.risk_view_state_nouns,
        read_words=settings.risk_read_words,
        session_phrases=settings.risk_session_phrases,
    )


def recording_config(settings: Settings) -> RecordingConfig:
    """The recorder's configuration, taken from Settings."""
    return RecordingConfig(
        step_timeout_ms=settings.step_timeout_ms,
        settle_timeout_ms=settings.settle_timeout_ms,
        settle_quiet_frames=settings.settle_quiet_frames,
        navigation_timeout_ms=settings.navigation_timeout_ms,
        checkpoint_timeout_ms=settings.record_checkpoint_timeout_ms,
        scope_ancestors_max=settings.record_scope_ancestors_max,
        landmarks_max=settings.record_landmarks_max,
        retry=replay_config(settings).retry,
        risk=risk_vocabulary(settings),
    )


def recording_options(settings: Settings) -> RecordingOptions:
    """How a recording's browser context is set up."""
    return RecordingOptions(
        viewport_width=settings.viewport_width,
        viewport_height=settings.viewport_height,
        default_timeout_ms=settings.step_timeout_ms,
    )


def record_launch_options(settings: Settings, *, slow_mo_ms: int | None) -> LaunchOptions:
    """A recording is always headed: a person performs the task in the window."""
    return LaunchOptions(
        headless=False,
        slow_mo_ms=settings.browser_slow_mo_ms if slow_mo_ms is None else slow_mo_ms,
    )


@asynccontextmanager
async def model_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient | None]:
    """An HTTP client for the configured model provider, or None when no model is configured.

    Creating the client opens no connection: a run that never reaches Rung 3 sends nothing.
    """
    if settings.model_provider is ModelProvider.NONE:
        yield None
        return
    async with httpx.AsyncClient() as client:
        yield client


def model_choice_config(settings: Settings) -> ModelChoiceConfig:
    """How Rung 3 asks the configured model."""
    return ModelChoiceConfig(
        candidates_k=settings.model_candidates_k,
        timeout_ms=settings.model_timeout_ms,
        provider=settings.model_provider.value,
        model=settings.model_name or settings.model_provider.value,
    )


def budget_limits(settings: Settings) -> BudgetLimits:
    """The model-call budgets, taken from Settings."""
    return BudgetLimits(
        per_run=settings.model_max_calls_per_run, per_day=settings.model_max_calls_per_day
    )


def model_rung(
    settings: Settings, *, client: httpx.AsyncClient, ledger_directory: Path
) -> ModelRung | None:
    """Rung 3 with the configured provider, or None when no model is configured."""
    model = chat_model(settings, client=client)
    if model is None:
        return None
    return ModelRung(
        model=model,
        config=model_choice_config(settings),
        limits=budget_limits(settings),
        ledger=FileUsageLedger(ledger_directory),
    )


def chat_model(settings: Settings, *, client: httpx.AsyncClient) -> ModelPort | None:
    """The configured provider as a ModelPort, or None when no model is configured."""
    name = settings.model_name
    if settings.model_provider is ModelProvider.NONE or name is None:
        return None
    timer = AsyncioTimer()
    transport = ModelHttpClient(
        client=client,
        policy=TransportPolicy(
            max_attempts=settings.model_max_attempts,
            retry=RetryPolicy(
                max_attempts=settings.model_max_attempts,
                initial_delay_ms=settings.model_retry_initial_delay_ms,
                max_delay_ms=settings.model_retry_max_delay_ms,
                multiplier=settings.retry_backoff_multiplier,
                jitter_ratio=settings.retry_jitter_ratio,
            ),
            max_response_bytes=settings.model_max_response_bytes,
        ),
        timer=timer,
        randomness=SystemRandomSource(),
    )
    return HttpChoiceModel(
        wire=wire_format(settings, name),
        model=name,
        transport=transport,
        breaker=CircuitBreaker(
            failure_threshold=settings.model_breaker_failures,
            reset_ms=settings.model_breaker_reset_ms,
            timer=timer,
        ),
        price=settings.model_prices.get(name),
        timer=timer,
    )


def wire_format(settings: Settings, name: str) -> WireFormat:
    """The configured provider's request and response shapes."""
    endpoint = settings.model_endpoint()
    provider = settings.model_provider
    if endpoint is None or provider is ModelProvider.NONE:
        raise MendworkError("no model provider is configured", provider=provider.value)
    match provider:
        case ModelProvider.OLLAMA:
            return OllamaWire(
                OllamaOptions(
                    base_url=endpoint,
                    model=name,
                    temperature=settings.model_temperature,
                    seed=settings.model_seed,
                    max_output_tokens=settings.model_max_output_tokens,
                    context_tokens=settings.model_context_tokens,
                    keep_alive=settings.model_keep_alive,
                    think=settings.model_think,
                )
            )
        case ModelProvider.GEMINI:
            if settings.model_api_key is None:
                raise MendworkError("the Gemini provider needs MENDWORK_MODEL_API_KEY")
            return GeminiWire(
                GeminiOptions(
                    base_url=endpoint,
                    model=name,
                    api_key=settings.model_api_key,
                    temperature=settings.model_temperature,
                    seed=settings.model_seed,
                    max_output_tokens=settings.model_max_output_tokens,
                    think=settings.model_think,
                )
            )
        case ModelProvider.OPENAI_COMPATIBLE:
            return OpenAICompatibleWire(
                OpenAICompatibleOptions(
                    base_url=endpoint,
                    model=name,
                    api_key=settings.model_api_key,
                    temperature=settings.model_temperature,
                    seed=settings.model_seed,
                    max_output_tokens=settings.model_max_output_tokens,
                    local=settings.model_local,
                )
            )


def egress_enforcement(settings: Settings, resolver: HostResolver) -> EgressEnforcement:
    """How each browser session holds its run to the egress policy (ADR 0011).

    A connection's handshake, name lookup, and connect attempts are bounded by the navigation
    timeout, the longest a page load may take.
    """
    return EgressEnforcement(resolver=resolver, timeout_ms=settings.navigation_timeout_ms)


def build_replayer(
    settings: Settings,
    *,
    launcher: BrowserLauncher,
    artifacts: LocalArtifactStore,
    events: EventSink,
    environ: Mapping[str, str],
    egress: EgressPolicy,
    resolver: HostResolver,
    scrubber: SecretScrubber,
    model: ModelRung | None = None,
) -> Replayer:
    """A Replayer on the real clock, timer, randomness, and environment secrets.

    Every secret it resolves is registered with ``scrubber``, the process's, so no log line the
    process writes can carry it (ADR 0011).
    """
    clock = SystemClock()
    return Replayer(
        launcher=launcher,
        artifacts=artifacts,
        records=LocalRunRecords(artifacts),
        events=events,
        secrets=RegisteringSecretResolver(EnvSecretResolver(environ), scrubber),
        clock=clock,
        timer=AsyncioTimer(),
        randomness=SystemRandomSource(),
        run_ids=TimestampRunIds(clock),
        config=replay_config(settings),
        egress=egress,
        resolver=resolver,
        model=model,
    )
