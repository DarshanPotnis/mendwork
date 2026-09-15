"""Replaying a workflow in-process on the test session's Chromium, with the CLI's own wiring.

A test can prepare the run's page before the first step (install routes, set content) and
inspect it after the last step, before its browser context closes. Inspection is how a
test reads `window.__chaos`; Mendwork itself never does.

Every run is held to an egress policy. By default it is the loopback exceptions for the local
servers the run's inputs name; a test serving pages through routes under a made-up host passes
that host as ``domains``. Names resolve through a fake resolver, so nothing reaches real DNS.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Browser, Page

from benchmarks.chaos.local_egress import local_policy
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.adapters.secrets_env.naming import secret_variable_name
from mendwork.apps.cli.wiring import build_replayer, egress_enforcement, session_options
from mendwork.engine.domain.events import RunEvent
from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.domain.runs import Run, RunId, StepResult
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings
from tests.fakes.egress import FakeResolver
from tests.fakes.ports import RecordingEventSink

PageHook = Callable[[Page], Awaitable[None]]


@dataclass(frozen=True)
class ReplayOutcome:
    """What a replay produced: its record, its events, and where its artifacts are."""

    run: Run
    events: tuple[RunEvent, ...]
    run_directory: Path

    def step(self, step_id: str) -> StepResult:
        return next(step for step in self.run.steps if step.step_id == step_id)

    def events_for(self, step_id: str, kind: str) -> list[RunEvent]:
        return [
            event
            for event in self.events
            if event.type == kind and getattr(event, "step_id", None) == step_id
        ]


class _HookedLauncher:
    def __init__(
        self, inner: PlaywrightLauncher, prepare: PageHook | None, inspect: PageHook | None
    ) -> None:
        self._inner = inner
        self._prepare = prepare
        self._inspect = inspect

    @asynccontextmanager
    async def session(
        self, run_id: RunId, egress: EgressPolicy
    ) -> AsyncIterator[PlaywrightSession]:
        async with self._inner.session(run_id, egress) as session:
            if self._prepare is not None:
                await self._prepare(session.page)
            try:
                yield session
            finally:
                if self._inspect is not None:
                    await self._inspect(session.page)


def replay_settings(**overrides: int | bool) -> Settings:
    """Settings for a test replay: the defaults, with any timing overrides applied."""
    return Settings(_env_file=None).model_copy(update=overrides)


async def replay(
    browser: Browser,
    workflow: WorkflowVersion,
    inputs: Mapping[str, str],
    directory: Path,
    *,
    secrets: Mapping[str, str] | None = None,
    settings: Settings | None = None,
    prepare: PageHook | None = None,
    inspect: PageHook | None = None,
    domains: Iterable[str] = (),
) -> ReplayOutcome:
    """Replay a workflow exactly as `mendwork run` would, on the given browser."""
    config = settings or replay_settings()
    artifacts = LocalArtifactStore(directory / "artifacts")
    resolver = FakeResolver()
    inner = await PlaywrightLauncher.create(
        browser, session_options(config), egress_enforcement(config, resolver)
    )
    events = RecordingEventSink()
    environ = {
        secret_variable_name(SecretName(name)): value for name, value in (secrets or {}).items()
    }
    replayer = build_replayer(
        config,
        launcher=_HookedLauncher(inner, prepare, inspect),
        artifacts=artifacts,
        events=events,
        environ=environ,
        egress=local_policy(inputs.values(), domains=domains),
        resolver=resolver,
        scrubber=SecretScrubber(),
    )
    run = await replayer.run(workflow, inputs)
    return ReplayOutcome(run, tuple(events.events), artifacts.run_directory(run.run_id))
