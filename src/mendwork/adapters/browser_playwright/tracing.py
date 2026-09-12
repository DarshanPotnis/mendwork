"""Playwright tracing that never records a secret, and proves it before a trace is kept.

Traces record action arguments (including the text typed by a fill) and DOM snapshots
(including values typed into fields). So:

1. Before a secret is typed, the current trace chunk is discarded and recording pauses.
   The document the secret was typed into is remembered as tainted.
2. Recording resumes only once a different document is showing. Resuming while the
   tainted document is still loaded is not enough: the next action's snapshot of that
   document would contain the typed value.
3. A failure on a tainted document keeps no trace at all.
4. A trace that is kept is scanned, member by member, for every registered secret in
   every covered encoding, and deleted if anything is found.
"""

import asyncio
import zipfile
from collections.abc import Awaitable
from enum import Enum, auto
from pathlib import Path

import structlog
from playwright.async_api import BrowserContext
from playwright.async_api import Error as PlaywrightError

from mendwork.adapters.browser_playwright.errors import browser_closed, is_closed
from mendwork.engine.domain.runs import TraceWithheldReason
from mendwork.engine.ports.browser_types import (
    TraceDisabled,
    TraceExport,
    TraceNotSaved,
    TraceSaved,
)
from mendwork.engine.safety.secret_scrub import SecretScrubber


class _State(Enum):
    OFF = auto()
    RECORDING = auto()
    PAUSED = auto()
    FINISHED = auto()


class TraceRecorder:
    """The tracing state of one browser context."""

    def __init__(self, context: BrowserContext, workdir: Path, *, enabled: bool) -> None:
        self._context = context
        self._workdir = workdir
        self._enabled = enabled
        self._state = _State.OFF
        self._tainted: str | None = None
        self._log = structlog.stdlib.get_logger("mendwork.browser.tracing")

    @property
    def paused(self) -> bool:
        """Whether recording waits for the tainted document to go away."""
        return self._state is _State.PAUSED

    async def start(self) -> None:
        """Begin recording, if tracing is enabled."""
        if self._enabled:
            await self._context.tracing.start(snapshots=True, screenshots=True, sources=False)
            self._state = _State.RECORDING

    async def pause_for_secret(self, document: str) -> None:
        """Discard what was recorded and stop, because a secret is about to be typed."""
        if self._state is _State.RECORDING:
            await self._call(self._context.tracing.stop_chunk())
            self._state = _State.PAUSED
        if self._state is _State.PAUSED:
            self._tainted = document

    async def observe_document(self, document: str) -> None:
        """Resume recording once the document showing is not the tainted one."""
        if self._state is _State.PAUSED and document != self._tainted:
            await self._call(self._context.tracing.start_chunk())
            self._state = _State.RECORDING
            self._tainted = None

    async def export(self, scrubber: SecretScrubber) -> TraceExport:
        """Keep the trace recorded since the last clean document, if it is provably clean."""
        match self._state:
            case _State.OFF | _State.FINISHED:
                return TraceDisabled()
            case _State.PAUSED:
                return TraceNotSaved(reason=TraceWithheldReason.SECRET_BEARING_PAGE)
            case _State.RECORDING:
                path = self._workdir / "trace.zip"
                await self._call(self._context.tracing.stop_chunk(path=path))
                self._state = _State.FINISHED
                if scrubber.active and await asyncio.to_thread(_holds_secret, path, scrubber):
                    await asyncio.to_thread(path.unlink)
                    self._log.error("trace_deleted_secret_detected")
                    return TraceNotSaved(reason=TraceWithheldReason.SECRET_DETECTED)
                return TraceSaved(path=path)

    async def stop(self) -> None:
        """Stop tracing without saving anything further."""
        if self._state is not _State.OFF:
            self._state = _State.OFF
            try:
                await self._context.tracing.stop()
            except PlaywrightError as error:
                # A browser that already closed has no trace left to stop.
                if not is_closed(error):
                    raise

    async def _call(self, awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
        except PlaywrightError as error:
            if is_closed(error):
                raise browser_closed(error) from error
            raise


def _holds_secret(path: Path, scrubber: SecretScrubber) -> bool:
    with zipfile.ZipFile(path) as archive:
        return any(scrubber.contains_secret(archive.read(member)) for member in archive.namelist())
