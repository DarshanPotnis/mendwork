"""Recording in-process on the test session's Chromium, driven by a scripted person.

The recorder, adapter, and wiring are the ones ``mendwork record`` uses. A scripted person
acts on the recording page with ordinary Playwright calls, waiting for the recorder's own
notices (never for time) before each next action. The inbound transcript keeps every
payload the recording page sent to Python, so a test can prove what never arrived.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from playwright.async_api import Browser, Page

from mendwork.adapters.browser_playwright.recording.launcher import PlaywrightRecordingLauncher
from mendwork.adapters.browser_playwright.recording.session import PlaywrightRecordingSession
from mendwork.adapters.system.randomness import SystemRandomSource
from mendwork.adapters.system.timer import AsyncioTimer
from mendwork.apps.cli.wiring import recording_config, recording_options
from mendwork.engine.domain.recording import (
    InteractionIgnored,
    NavigationIgnored,
    Recording,
    RecordingNotice,
    StepRecorded,
)
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import RecordingUnusable
from mendwork.engine.recording.assembly import assemble
from mendwork.engine.recording.naming import Decisions, NamingSession
from mendwork.engine.recording.recorder import Recorder
from mendwork.settings import Settings
from tests.fakes.clock import FakeClock
from tests.integration.replay_harness import replay_settings

WAIT_SECONDS: Final = 30
RECORDED_AT: Final = FakeClock(datetime(2026, 9, 12, 0, 0, tzinfo=UTC))

PageHook = Callable[[Page], Awaitable[None]]


class NoticeLog:
    """A RecordingObserver a scripted person can wait on."""

    def __init__(self) -> None:
        self.notices: list[RecordingNotice] = []
        self._changed = asyncio.Condition()

    async def notify(self, notice: RecordingNotice) -> None:
        async with self._changed:
            self.notices.append(notice)
            self._changed.notify_all()

    @property
    def steps_recorded(self) -> int:
        """How many steps exist now; a fill that replaced another does not add one."""
        return sum(
            1 for notice in self.notices if isinstance(notice, StepRecorded) and not notice.replaced
        )

    async def wait_for(self, predicate: Callable[[], bool]) -> None:
        async with asyncio.timeout(WAIT_SECONDS), self._changed:
            await self._changed.wait_for(predicate)


class Transcript:
    """An InboundObserver that keeps every payload the recording page sent to Python."""

    def __init__(self) -> None:
        self.payloads: list[tuple[str, object]] = []

    def received(self, source: str, payload: object) -> None:
        self.payloads.append((source, payload))


class EventStop:
    """A stop signal the harness presses when the scripted person is done."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def press(self) -> None:
        self._event.set()


@dataclass
class ScriptedUser:
    """The person doing the task: the recording page, and a way to wait for the recorder."""

    page: Page
    log: NoticeLog

    async def steps(self, count: int) -> None:
        """Wait until the recorder has recorded at least ``count`` steps."""
        await self.log.wait_for(lambda: self.log.steps_recorded >= count)

    async def ignored(self, count: int) -> None:
        """Wait until at least ``count`` interactions were ignored."""
        await self.log.wait_for(
            lambda: sum(isinstance(item, InteractionIgnored) for item in self.log.notices) >= count
        )

    async def navigation_noted(self) -> None:
        """Wait until the recorder noted a navigation the page made by itself."""
        await self.log.wait_for(
            lambda: any(isinstance(item, NavigationIgnored) for item in self.log.notices)
        )


Script = Callable[[ScriptedUser], Awaitable[None]]


@dataclass
class RecordingOutcome:
    """What a scripted recording produced."""

    recording: Recording | None
    error: RecordingUnusable | None
    notices: list[RecordingNotice]
    transcript: list[tuple[str, object]]
    page_scripts_seen: list[str] = field(default_factory=list)

    @property
    def recorded(self) -> Recording:
        assert self.recording is not None, self.error
        return self.recording

    def decisions(self, answers: dict[str, str] | None = None) -> Decisions:
        """Every proposal accepted, except proposals whose default name is answered."""
        session = NamingSession(self.recorded.steps, [])
        for secret in session.secret_proposals:
            session.name_secret(secret, (answers or {}).get(secret.default_name, ""))
        for proposal in session.input_proposals:
            session.name_input(proposal, (answers or {}).get(proposal.default_name, ""))
        return session.decisions()

    def workflow(self, workflow_id: str, decisions: Decisions | None = None) -> WorkflowVersion:
        return assemble(
            self.recorded,
            decisions or self.decisions(),
            workflow_id=workflow_id,
            clock=RECORDED_AT,
        )


class _ScriptedLauncher:
    def __init__(
        self,
        inner: PlaywrightRecordingLauncher,
        script: Script,
        log: NoticeLog,
        stop: EventStop,
        prepare: PageHook | None,
    ) -> None:
        self._inner = inner
        self._script = script
        self._log = log
        self._stop = stop
        self._prepare = prepare
        self.task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def recording_session(self) -> AsyncIterator[PlaywrightRecordingSession]:
        async with self._inner.recording_session() as session:
            if self._prepare is not None:
                await self._prepare(session.page)
            self.task = asyncio.ensure_future(self._run(session.page))
            try:
                yield session
            finally:
                if not self.task.done():
                    self.task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.task

    async def _run(self, page: Page) -> None:
        user = ScriptedUser(page, self._log)
        await user.steps(1)
        await self._script(user)
        self._stop.press()


async def record_scripted(
    browser: Browser,
    start_url: str,
    script: Script,
    *,
    prepare: PageHook | None = None,
    settings: Settings | None = None,
) -> RecordingOutcome:
    """Record with a scripted person, exactly as ``mendwork record`` records."""
    config = settings or replay_settings()
    transcript = Transcript()
    log = NoticeLog()
    stop = EventStop()
    inner = await PlaywrightRecordingLauncher.create(browser, recording_options(config), transcript)
    launcher = _ScriptedLauncher(inner, script, log, stop, prepare)
    recorder = Recorder(
        launcher=launcher,
        observer=log,
        timer=AsyncioTimer(),
        randomness=SystemRandomSource(),
        config=recording_config(config),
    )
    recording: Recording | None = None
    error: RecordingUnusable | None = None
    try:
        recording = await recorder.record(start_url, stop)
    except RecordingUnusable as unusable:
        error = unusable
    task = launcher.task
    if error is None and task is not None and task.done() and not task.cancelled():
        failure = task.exception()
        if failure is not None:
            raise failure
    return RecordingOutcome(recording, error, log.notices, transcript.payloads)
