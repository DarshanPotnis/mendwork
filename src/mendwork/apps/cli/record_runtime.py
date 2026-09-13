"""What ``mendwork record`` runs on for real: Chromium, Ctrl+C, the clock, and a headed replay.

Kept apart from the command's flow (``record.py``) so tests compose the same flow from fakes.
"""

import asyncio
import os
import signal
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TextIO

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import ChromiumLauncher
from mendwork.adapters.browser_playwright.recording.channel import LoggingInbound
from mendwork.adapters.browser_playwright.recording.launcher import ChromiumRecordingLauncher
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.system.randomness import SystemRandomSource
from mendwork.adapters.system.timer import AsyncioTimer
from mendwork.apps.cli.human_output import HumanProgress, render_summary
from mendwork.apps.cli.record import RecordDependencies
from mendwork.apps.cli.wiring import (
    build_replayer,
    launch_options,
    record_launch_options,
    recording_options,
    session_options,
)
from mendwork.engine.domain.runs import Run
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.ports.recording import RecordingLauncher
from mendwork.settings import Settings


class InterruptStop:
    """Ctrl+C stops recording after the step in progress; a second Ctrl+C aborts it."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def connected(self) -> AbstractAsyncContextManager[None]:
        return self._connected()

    @asynccontextmanager
    async def _connected(self) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()

        def interrupted() -> None:
            if self._event.is_set() and task is not None:
                task.cancel()
                return
            self._event.set()

        loop.add_signal_handler(signal.SIGINT, interrupted)
        try:
            yield
        finally:
            loop.remove_signal_handler(signal.SIGINT)


def production_dependencies() -> RecordDependencies:
    """The real browser, the real terminal, and the real clock."""
    return RecordDependencies(
        open_recorder=chromium_recorder,
        verify=replay_recording,
        stop=InterruptStop,
        clock=SystemClock(),
        timer=AsyncioTimer,
        randomness=SystemRandomSource,
    )


def chromium_recorder(
    settings: Settings, slow_mo_ms: int | None
) -> AbstractAsyncContextManager[RecordingLauncher]:
    """A headed Chromium with the recorder installed, launched when recording starts."""
    return ChromiumRecordingLauncher(
        record_launch_options(settings, slow_mo_ms=slow_mo_ms),
        recording_options(settings),
        LoggingInbound(),
    )


async def replay_recording(
    version: WorkflowVersion,
    inputs: Mapping[str, str],
    settings: Settings,
    *,
    slow_mo_ms: int | None,
    stdout: TextIO,
) -> Run:
    """Replay a recorded workflow exactly as ``mendwork run --headed`` would."""
    artifacts = LocalArtifactStore(settings.artifacts_dir)
    events = HumanProgress(stdout, artifacts.runs_root)
    launcher = ChromiumLauncher(
        launch_options(settings, headed=True, slow_mo_ms=slow_mo_ms), session_options(settings)
    )
    async with launcher:
        replayer = build_replayer(
            settings, launcher=launcher, artifacts=artifacts, events=events, environ=os.environ
        )
        run = await replayer.run(version, inputs)
    stdout.write(render_summary(run, artifacts.runs_root) + "\n")
    return run
