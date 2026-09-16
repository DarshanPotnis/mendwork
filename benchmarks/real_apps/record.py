"""Recording a workflow on release A, driven by a scripted person (ADR 0014).

The recorder, its adapter, and its wiring are the ones ``mendwork record`` uses, so the workflow's
selectors and fingerprints are derived exactly as a person's recording would derive them. Only the
person is scripted: each action is an ordinary Playwright call, and each waits for the recorder's
own notice before the next, so no step races the one before it.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Final

from playwright.async_api import Browser, Page

from mendwork.adapters.browser_playwright.recording.launcher import PlaywrightRecordingLauncher
from mendwork.adapters.browser_playwright.recording.session import PlaywrightRecordingSession
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.system.randomness import SystemRandomSource
from mendwork.adapters.system.timer import AsyncioTimer
from mendwork.apps.cli.wiring import recording_config, recording_options
from mendwork.engine.domain.recording import Recording, RecordingNotice, StepRecorded
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.recording.assembly import assemble
from mendwork.engine.recording.naming import Decisions, NamingSession
from mendwork.engine.recording.recorder import Recorder
from mendwork.settings import Settings

WAIT_SECONDS: Final = 60


class NoticeLog:
    """A RecordingObserver the scripted person waits on."""

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


class Stop:
    """The stop a person presses when the task is done."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def press(self) -> None:
        self._event.set()


class Silent:
    """An InboundObserver that keeps nothing: a recording of a real application is not inspected."""

    def received(self, source: str, payload: object) -> None:
        return None


@dataclass
class ScriptedPerson:
    """The person doing the task: the recording page, and a way to wait for the recorder."""

    page: Page
    log: NoticeLog

    async def steps(self, count: int) -> None:
        """Wait until the recorder has recorded at least ``count`` steps."""
        await self.log.wait_for(lambda: self.log.steps_recorded >= count)


Script = Callable[[ScriptedPerson], Awaitable[None]]


class _ScriptedLauncher:
    """The recording launcher, with the scripted person acting on the page it opens."""

    def __init__(
        self, inner: PlaywrightRecordingLauncher, script: Script, log: NoticeLog, stop: Stop
    ) -> None:
        self._inner = inner
        self._script = script
        self._log = log
        self._stop = stop
        self.task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def recording_session(self) -> AsyncIterator[PlaywrightRecordingSession]:
        async with self._inner.recording_session() as session:
            self.task = asyncio.ensure_future(self._run(session.page))
            try:
                yield session
            finally:
                if not self.task.done():
                    self.task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.task

    async def _run(self, page: Page) -> None:
        # The stop is pressed however the script ends: a script that fails must end the recording,
        # so the failure is reported instead of the recorder waiting for a person who is not there.
        try:
            person = ScriptedPerson(page, self._log)
            await person.steps(1)
            await self._script(person)
        finally:
            self._stop.press()


def decisions_for(recording: Recording, names: dict[str, str]) -> Decisions:
    """Accept every proposal, naming inputs and secrets as the pair asked."""
    session = NamingSession(recording.steps, [])
    for secret in session.secret_proposals:
        session.name_secret(secret, names.get(secret.default_name, ""))
    for proposal in session.input_proposals:
        session.name_input(proposal, names.get(proposal.default_name, ""))
    return session.decisions()


async def record_workflow(
    browser: Browser,
    start_url: str,
    script: Script,
    *,
    workflow_id: str,
    names: dict[str, str],
    settings: Settings,
) -> WorkflowVersion:
    """Record the task on the running release and assemble it into a workflow version."""
    log = NoticeLog()
    stop = Stop()
    inner = await PlaywrightRecordingLauncher.create(browser, recording_options(settings), Silent())
    launcher = _ScriptedLauncher(inner, script, log, stop)
    recorder = Recorder(
        launcher=launcher,
        observer=log,
        timer=AsyncioTimer(),
        randomness=SystemRandomSource(),
        config=recording_config(settings),
    )
    recording = await recorder.record(start_url, stop)
    task = launcher.task
    if task is not None and task.done() and not task.cancelled():
        failure = task.exception()
        if failure is not None:
            raise failure
    return assemble(
        recording, decisions_for(recording, names), workflow_id=workflow_id, clock=SystemClock()
    )
