"""A scripted RecordingBrowser for recorder tests: page events and element facts as data.

Extends the replay fake: selectors, identities, and actions behave exactly as there. A test
adds the events the page would send, the facts each element would report, what the page
looks like to an observation, and the navigations an action commits.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from mendwork.engine.errors import MendworkError, TargetNotFound
from mendwork.engine.ports.browser_types import ElementRef, WatchId
from mendwork.engine.ports.recording import StopSignal
from mendwork.engine.ports.recording_types import (
    AncestorFacts,
    CaptureRef,
    ElementFacts,
    FieldText,
    Landmark,
    NavigationCommitted,
    NavigationInitiator,
    NavigationRecord,
    PageEvent,
    PageObservation,
)
from tests.fakes.browser import FakeBrowser


@dataclass
class FakeRecordingBrowser(FakeBrowser):
    """A recording page written by the test."""

    events: list[PageEvent] = field(default_factory=list)
    """Returned in order by next_event; None once empty."""
    pending: list[PageEvent] = field(default_factory=list)
    """Returned by flush_pending."""
    captured: dict[tuple[str, int], str] = field(default_factory=dict)
    """(document, page element key) to the element key in ``elements``."""
    facts: dict[str, ElementFacts] = field(default_factory=dict)
    ancestors: dict[str, tuple[AncestorFacts, ...]] = field(default_factory=dict)
    field_texts: dict[str, FieldText] = field(default_factory=dict)
    detached: set[str] = field(default_factory=set)
    """Elements whose facts can no longer be read."""
    title: str = ""
    landmarks: tuple[Landmark, ...] = ()
    live_texts: tuple[str, ...] = ()
    navigation_records: list[NavigationRecord] = field(default_factory=list)
    arm_succeeds: bool = True
    armed: list[CaptureRef] = field(default_factory=list)
    disarmed: list[CaptureRef] = field(default_factory=list)
    finished: list[CaptureRef] = field(default_factory=list)
    field_reads: list[str] = field(default_factory=list)
    flushes: int = 0

    def commit_navigation(
        self, url: str, initiator: NavigationInitiator = NavigationInitiator.PAGE
    ) -> NavigationRecord:
        """A new document committed: the URL changes, and the navigation is recorded."""
        record = NavigationRecord(
            sequence=len(self.navigation_records) + 1, url=url, initiator=initiator
        )
        self.navigation_records.append(record)
        self.url = url
        self.document += 1
        self.epoch += 1
        return record

    async def next_event(self, stop: StopSignal) -> PageEvent | None:
        if not self.events:
            return None
        event = self.events.pop(0)
        if isinstance(event, NavigationCommitted) and event.record not in self.navigation_records:
            # The adapter logs a commit when it happens and queues its event; the page moves.
            self.navigation_records.append(event.record)
            self.url = event.record.url
            self.document += 1
            self.epoch += 1
        return event

    async def flush_pending(self, *, timeout_ms: int) -> tuple[PageEvent, ...]:
        self.flushes += 1
        pending, self.pending = self.pending, []
        return tuple(pending)

    async def pin_capture(self, ref: CaptureRef) -> ElementRef | None:
        key = self.captured.get((ref.document, ref.element or 0))
        if key is None:
            return None
        element = ElementRef(f"ref-{len(self._refs) + 1}")
        self._refs[element] = key
        return element

    async def arm(self, ref: CaptureRef) -> bool:
        self.armed.append(ref)
        return self.arm_succeeds

    async def disarm(self, ref: CaptureRef) -> None:
        self.disarmed.append(ref)

    async def finish_capture(self, ref: CaptureRef) -> None:
        self.finished.append(ref)

    async def element_facts(self, element: ElementRef) -> ElementFacts:
        key = self._refs[element]
        if key in self.detached:
            raise TargetNotFound("detached", reason="detached_while_recording")
        return self.facts[key]

    async def scope_ancestors(
        self, element: ElementRef, *, limit: int
    ) -> tuple[AncestorFacts, ...]:
        return self.ancestors.get(self._refs[element], ())[:limit]

    async def read_field_text(self, element: ElementRef) -> FieldText:
        key = self._refs[element]
        self.field_reads.append(key)
        return self.field_texts[key]

    async def observe_page(self, *, limit: int) -> PageObservation:
        return PageObservation(
            url=self.url,
            title=self.title,
            navigation=len(self.navigation_records),
            landmarks=self.landmarks[:limit],
            live_texts=self.live_texts,
        )

    async def navigations_since(self, sequence: int) -> tuple[NavigationRecord, ...]:
        return tuple(record for record in self.navigation_records if record.sequence > sequence)

    async def downloads_started(self, watch: WatchId) -> int:
        return len(self._downloads.get(watch, []))


@dataclass
class FakeRecordingLauncher:
    """Hands out one recording browser, or fails to."""

    browser: FakeRecordingBrowser
    error: MendworkError | None = None
    opened: int = 0
    closed: int = 0

    @asynccontextmanager
    async def recording_session(self) -> AsyncIterator[FakeRecordingBrowser]:
        if self.error is not None:
            raise self.error
        self.opened += 1
        try:
            yield self.browser
        finally:
            self.closed += 1


class NeverStop:
    """A stop signal nobody presses; recordings end when the page runs out of events."""

    @property
    def requested(self) -> bool:
        return False

    async def wait(self) -> None:
        raise AssertionError("a fake recording waited for a stop that never comes")


class Stopped:
    """A stop signal already pressed."""

    @property
    def requested(self) -> bool:
        return True

    async def wait(self) -> None:
        return None
