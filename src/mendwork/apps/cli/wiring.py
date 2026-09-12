"""Composition: Settings and adapters assembled into a Replayer.

Shared by ``mendwork run`` and the benchmark scripts, so both replay exactly the same way.
"""

from collections.abc import Mapping

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import LaunchOptions, SessionOptions
from mendwork.adapters.secrets_env.resolver import EnvSecretResolver
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.system.randomness import SystemRandomSource
from mendwork.adapters.system.run_ids import TimestampRunIds
from mendwork.adapters.system.timer import AsyncioTimer
from mendwork.engine.ports.browser import BrowserLauncher
from mendwork.engine.ports.events import EventSink
from mendwork.engine.replay.config import ReplayConfig, RetryPolicy
from mendwork.engine.replay.replayer import Replayer
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
