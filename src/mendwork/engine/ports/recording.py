"""The recording ports: a browser a person drives while the recorder watches and verifies.

A recording browser is a BrowserPort plus what recording adds: a stream of page events,
element facts, and the handshake that lets the page hold back a click or key until the
recorder has verified its target and performs it itself.
"""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from mendwork.engine.domain.recording import RecordingNotice
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import ElementRef, WatchId
from mendwork.engine.ports.recording_types import (
    AncestorFacts,
    CaptureRef,
    FieldText,
    NavigationRecord,
    PageEvent,
    PageObservation,
)


class StopSignal(Protocol):
    """How the person says they are done, such as Ctrl+C in the terminal."""

    @property
    def requested(self) -> bool:
        """Whether stopping was requested."""
        ...

    async def wait(self) -> None:
        """Return once stopping is requested."""
        ...


class RecordingBrowser(BrowserPort, Protocol):
    """One recording's page."""

    async def next_event(self, stop: StopSignal) -> PageEvent | None:
        """The next page event in order, or None once stopping is requested."""
        ...

    async def flush_pending(self, *, timeout_ms: int) -> tuple[PageEvent, ...]:
        """Ask the page to commit edited fields, then return every event still queued."""
        ...

    async def pin_capture(self, ref: CaptureRef) -> ElementRef | None:
        """The captured element, pinned; None if its document or element is gone."""
        ...

    async def arm(self, ref: CaptureRef) -> bool:
        """Let the recorder's own action reach the captured element; False if it is gone."""
        ...

    async def disarm(self, ref: CaptureRef) -> None:
        """Hold back interactions again until the capture is finished."""
        ...

    async def finish_capture(self, ref: CaptureRef) -> None:
        """Release the page: the capture is recorded, ignored, or abandoned."""
        ...

    async def scope_ancestors(
        self, element: ElementRef, *, limit: int
    ) -> tuple[AncestorFacts, ...]:
        """Up to ``limit`` ancestors of the element, nearest first."""
        ...

    async def read_field_text(self, element: ElementRef) -> FieldText:
        """A field's content, or MaskedField if the field is masked when read."""
        ...

    async def observe_page(self, *, limit: int) -> PageObservation:
        """The page's URL, title, visible headings and landmarks, and live region texts."""
        ...

    async def navigations_since(self, sequence: int) -> tuple[NavigationRecord, ...]:
        """Main-frame documents committed after the given navigation sequence number."""
        ...

    async def downloads_started(self, watch: WatchId) -> int:
        """How many downloads have started since the watch began, without waiting."""
        ...


class RecordingLauncher(Protocol):
    """Opens the browser a person records in."""

    def recording_session(self) -> AbstractAsyncContextManager[RecordingBrowser]:
        """A recording session, closed when the context exits."""
        ...


class RecordingObserver(Protocol):
    """Hears about every step and every ignored interaction as it happens."""

    async def notify(self, notice: RecordingNotice) -> None:
        """Deliver one notice."""
        ...


def element_key(ref: CaptureRef) -> str | None:
    """A stable key for the element a capture used, within its document."""
    return None if ref.element is None else f"{ref.document}:{ref.element}"
