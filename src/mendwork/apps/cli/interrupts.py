"""Ctrl+C and SIGTERM while a command executes a run (ADR 0011).

The first signal cancels the run's task: the run stops where it is, records itself as cancelled or
needing review, closes its browser, and the command exits with the record's exit code. A second
signal does not wait for any of that: ``abort_run`` finishes the record from the journal on disk
and ends the process at once; Playwright's driver exits with it and takes Chromium along.

A short write that must stay whole (recording a person's decision) runs inside ``deferred``: a
first signal that arrives then takes effect when the write is done.
"""

import asyncio
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Final, TextIO

from pydantic import ValidationError

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.apps.cli.exit_codes import ExitCode, exit_code_for
from mendwork.engine.domain.events import RunEvent
from mendwork.engine.domain.runs import Run, RunId
from mendwork.engine.errors import MendworkError
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.events import EventSink
from mendwork.engine.replay.artifact_names import RUN_RECORD
from mendwork.engine.replay.journal import encode_run
from mendwork.engine.safety.interruption import Interruption, ended_run

_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM)


class RunInterrupts:
    """Turns the first interrupt into a cancellation, and a second into an immediate abort."""

    def __init__(self, abort: Callable[[], None]) -> None:
        self._abort = abort
        self._received = 0
        self._arrived = asyncio.Event()
        self._cancel: Callable[[], object] | None = None
        self._deferring = False
        self._pending = False

    @property
    def interrupted(self) -> bool:
        """Whether an interrupt arrived."""
        return self._received > 0

    async def arrived(self) -> None:
        """Wait until an interrupt has arrived and been handled."""
        await self._arrived.wait()

    @asynccontextmanager
    async def connected(self) -> AsyncIterator[None]:
        """Listen for SIGINT and SIGTERM while the context is open."""
        loop = asyncio.get_running_loop()
        for number in _SIGNALS:
            loop.add_signal_handler(number, self._signalled)
        try:
            yield
        finally:
            for number in _SIGNALS:
                loop.remove_signal_handler(number)

    def watch(self, cancel: Callable[[], object]) -> None:
        """What a first interrupt cancels; one that already arrived cancels it now."""
        self._cancel = cancel
        if self._received and not self._deferring:
            cancel()

    @asynccontextmanager
    async def deferred(self) -> AsyncIterator[None]:
        """A first interrupt arriving inside the context takes effect when the context ends."""
        self._deferring = True
        try:
            yield
        finally:
            self._deferring = False
            if self._pending:
                self._pending = False
                self._cancel_watched()

    def _signalled(self) -> None:
        self._received += 1
        if self._received > 1:
            self._abort()
        elif self._deferring:
            self._pending = True
        else:
            self._cancel_watched()
        self._arrived.set()

    def _cancel_watched(self) -> None:
        if self._cancel is not None:
            self._cancel()


class RunWitness:
    """An EventSink that passes every event on and remembers which run they belong to."""

    def __init__(self, inner: EventSink) -> None:
        self._inner = inner
        self.run_id: RunId | None = None

    async def emit(self, event: RunEvent) -> None:
        if self.run_id is None:
            self.run_id = event.run_id
        await self._inner.emit(event)


def abort_run(
    store: LocalArtifactStore,
    run_id: RunId | None,
    *,
    clock: Clock,
    stderr: TextIO,
    exit_process: Callable[[int], None],
) -> None:
    """Finish the run's record from its journal on disk and end the process at once.

    The in-progress step, whether its action started, and the events after this moment are not
    recorded; everything the journal had written stays true.
    """
    code: int = ExitCode.CANCELLED
    try:
        data = None if run_id is None else store.read_now(run_id, RUN_RECORD)
        if run_id is not None and data is not None:
            record = ended_run(
                Run.model_validate_json(data),
                at=clock.now(),
                interruption=Interruption.FORCED,
                step_in_progress=True,
            )
            store.write_now(run_id, RUN_RECORD, encode_run(record))
            code = exit_code_for(record)
        message = (
            "Aborted by a second interrupt: the run record was finished from what the run had "
            "written; the browser closes with this process."
        )
    except (MendworkError, ValidationError) as error:
        code = ExitCode.INFRASTRUCTURE
        message = (
            "Aborted by a second interrupt, but the run record could not be finished "
            f"({type(error).__name__}); `mendwork show` reports how far the run got."
        )
    stderr.write(message + "\n")
    stderr.flush()
    exit_process(int(code))
