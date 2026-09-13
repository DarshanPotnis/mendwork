"""Composition: Settings and adapters assembled into a Replayer.

Shared by ``mendwork run`` and the benchmark scripts, so both replay exactly the same way.
"""

from collections.abc import Mapping

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import LaunchOptions, SessionOptions
from mendwork.adapters.browser_playwright.recording.launcher import RecordingOptions
from mendwork.adapters.secrets_env.resolver import EnvSecretResolver
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.system.randomness import SystemRandomSource
from mendwork.adapters.system.run_ids import TimestampRunIds
from mendwork.adapters.system.timer import AsyncioTimer
from mendwork.engine.healing.config import FeatureWeights, HealingConfig
from mendwork.engine.ports.browser import BrowserLauncher
from mendwork.engine.ports.events import EventSink
from mendwork.engine.recording.config import RecordingConfig
from mendwork.engine.replay.config import ReplayConfig, RetryPolicy
from mendwork.engine.replay.replayer import Replayer
from mendwork.engine.safety.risk import RiskVocabulary
from mendwork.settings import Settings


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


def build_replayer(
    settings: Settings,
    *,
    launcher: BrowserLauncher,
    artifacts: LocalArtifactStore,
    events: EventSink,
    environ: Mapping[str, str],
) -> Replayer:
    """A Replayer on the real clock, timer, randomness, and environment secrets."""
    clock = SystemClock()
    return Replayer(
        launcher=launcher,
        artifacts=artifacts,
        events=events,
        secrets=EnvSecretResolver(environ),
        clock=clock,
        timer=AsyncioTimer(),
        randomness=SystemRandomSource(),
        run_ids=TimestampRunIds(clock),
        config=replay_config(settings),
    )
