"""Ground truth at the moment of action: is the element about to be acted on the real target?

Benchmark and test code may read ``window.__chaos``; Mendwork never does. ``GroundTruthSession``
delegates every BrowserPort call to the real Playwright session. Before each action it asks the
page whether the pinned element is the element the running step's chaos target key names,
through ``window.__chaos.locate``. After the run, the launcher reads the page's recorded wrong
actions before the browser context closes.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final

from playwright.async_api import Error as PlaywrightError
from pydantic import TypeAdapter

from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.engine.domain.checkpoints import ResponseReceived, UrlMatches
from mendwork.engine.domain.events import RunEvent, StepStartedEvent
from mendwork.engine.domain.runs import RunId
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.ports.browser_types import (
    Actionability,
    DomEpoch,
    DownloadObservation,
    ElementIdentity,
    ElementRef,
    FieldExpectation,
    FieldValueCheck,
    FillText,
    NavigationOutcome,
    ResponseObservation,
    Settling,
    TraceExport,
    UniqueMatch,
    WatchId,
    WatchKind,
)
from mendwork.engine.ports.candidate_types import CandidateQuery, CandidateScan, LiveCandidate
from mendwork.engine.ports.element_types import ElementFacts
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.egress_blocks import EgressBlock
from mendwork.engine.safety.secret_scrub import SecretScrubber

DEMO_EMAIL: Final = "buyer@harborline.test"
_IS_TARGET: Final = """([key, element]) => {
  const chaos = window.__chaos;
  if (chaos === undefined || chaos.pageId !== key.split(".")[0]) {
    return false;
  }
  try {
    return chaos.locate(key) === element;
  } catch {
    return false;
  }
}"""
_TARGET_INDEX: Final = """([key, elements]) => {
  const chaos = window.__chaos;
  if (chaos === undefined || chaos.pageId !== key.split(".")[0]) {
    return -1;
  }
  try {
    const target = chaos.locate(key);
    return elements.findIndex((element) => element === target);
  } catch {
    return -1;
  }
}"""
_WRONG_ACTIONS: Final = """() =>
  window.__chaos === undefined ? [] : window.__chaos.wrongActions.map((entry) => entry.label)"""
_SIGNED_IN: Final = (
    "sessionStorage.setItem('harborline.session', JSON.stringify({ email: '" + DEMO_EMAIL + "' }));"
)
_LABELS: Final[TypeAdapter[list[str]]] = TypeAdapter(list[str])


@dataclass(frozen=True, slots=True)
class ActionCheck:
    """One action, the step that performed it, and whether it reached the real target."""

    step_id: str | None
    action: str
    target_key: str | None
    correct: bool | None
    """None when the step has no ground-truth target (a navigate step, say)."""


class StepTracker:
    """An EventSink that keeps every event, knows which step is running, and, for benchmark models,
    the element that really is each step's target at its latest candidate scan."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []
        self.current: str | None = None
        self.truths: dict[str, LiveCandidate | None] = {}
        self.scans: dict[str, tuple[LiveCandidate, ...]] = {}

    async def emit(self, event: RunEvent) -> None:
        self.events.append(event)
        if isinstance(event, StepStartedEvent):
            self.current = event.step_id


class GroundTruthSession:
    """A BrowserPort that checks every action against the chaos ground truth first."""

    def __init__(
        self,
        inner: PlaywrightSession,
        targets: Mapping[str, str],
        tracker: StepTracker,
        checks: list[ActionCheck],
    ) -> None:
        self._inner = inner
        self._targets = targets
        self._tracker = tracker
        self._checks = checks

    async def _check(self, action: str, element: ElementRef) -> None:
        step_id = self._tracker.current
        key = self._targets.get(step_id) if step_id is not None else None
        if key is None:
            self._checks.append(ActionCheck(step_id, action, None, None))
            return
        handle = self._inner.pinned_handle(element)
        correct = bool(await self._inner.page.evaluate(_IS_TARGET, [key, handle]))
        self._checks.append(ActionCheck(step_id, action, key, correct))

    async def click(self, element: ElementRef, *, timeout_ms: int) -> None:
        await self._check("click", element)
        await self._inner.click(element, timeout_ms=timeout_ms)

    async def fill(self, element: ElementRef, value: FillText, *, timeout_ms: int) -> None:
        await self._check("fill", element)
        await self._inner.fill(element, value, timeout_ms=timeout_ms)

    async def select_option(self, element: ElementRef, label: str, *, timeout_ms: int) -> None:
        await self._check("select", element)
        await self._inner.select_option(element, label, timeout_ms=timeout_ms)

    async def press(self, element: ElementRef | None, key: str, *, timeout_ms: int) -> None:
        if element is not None:
            await self._check("press", element)
        await self._inner.press(element, key, timeout_ms=timeout_ms)

    async def navigate(self, url: str, *, timeout_ms: int) -> NavigationOutcome:
        return await self._inner.navigate(url, timeout_ms=timeout_ms)

    async def wait_for_url(self, checkpoint: UrlMatches, *, timeout_ms: int) -> bool:
        return await self._inner.wait_for_url(checkpoint, timeout_ms=timeout_ms)

    async def current_url(self) -> str:
        return await self._inner.current_url()

    async def take_opened_pages(self) -> int:
        return await self._inner.take_opened_pages()

    async def take_egress_blocks(self) -> tuple[EgressBlock, ...]:
        return await self._inner.take_egress_blocks()

    async def wait_until_settled(self, *, quiet_frames: int, timeout_ms: int) -> Settling:
        return await self._inner.wait_until_settled(
            quiet_frames=quiet_frames, timeout_ms=timeout_ms
        )

    async def dom_epoch(self, *, timeout_ms: int) -> DomEpoch:
        return await self._inner.dom_epoch(timeout_ms=timeout_ms)

    async def wait_for_dom_change(self, since: DomEpoch, *, timeout_ms: int) -> bool:
        return await self._inner.wait_for_dom_change(since, timeout_ms=timeout_ms)

    async def resolve_unique(self, selector: Selector) -> UniqueMatch:
        return await self._inner.resolve_unique(selector)

    async def group_identical(self, elements: Sequence[ElementRef]) -> tuple[int, ...]:
        return await self._inner.group_identical(elements)

    async def identify(self, element: ElementRef, *, confirm: bool) -> ElementIdentity:
        return await self._inner.identify(element, confirm=confirm)

    async def element_facts(self, element: ElementRef) -> ElementFacts:
        return await self._inner.element_facts(element)

    async def scan_candidates(self, query: CandidateQuery) -> CandidateScan:
        scan = await self._inner.scan_candidates(query)
        step_id = self._tracker.current
        key = self._targets.get(step_id) if step_id is not None else None
        if step_id is not None and key is not None:
            handles = [self._inner.pinned_handle(item.element) for item in scan.candidates]
            index = await self._inner.page.evaluate(_TARGET_INDEX, [key, handles])
            found = scan.candidates[index] if isinstance(index, int) and index >= 0 else None
            self._tracker.truths[step_id] = found
            self._tracker.scans[step_id] = scan.candidates
        return scan

    async def actionability(self, element: ElementRef) -> Actionability:
        return await self._inner.actionability(element)

    async def release(self, elements: Sequence[ElementRef]) -> None:
        await self._inner.release(elements)

    async def watch(self, kinds: frozenset[WatchKind]) -> WatchId:
        return await self._inner.watch(kinds)

    async def next_download(self, watch: WatchId, *, timeout_ms: int) -> DownloadObservation | None:
        return await self._inner.next_download(watch, timeout_ms=timeout_ms)

    async def next_response(
        self, watch: WatchId, checkpoint: ResponseReceived, *, timeout_ms: int
    ) -> ResponseObservation | None:
        return await self._inner.next_response(watch, checkpoint, timeout_ms=timeout_ms)

    async def unwatch(self, watch: WatchId) -> None:
        await self._inner.unwatch(watch)

    async def visible_text(self) -> str:
        return await self._inner.visible_text()

    async def visible_alert_texts(self) -> tuple[str, ...]:
        return await self._inner.visible_alert_texts()

    async def count_visible(self, selector: Selector) -> int:
        return await self._inner.count_visible(selector)

    async def wait_for_field_value(
        self, element: ElementRef, expected: FieldExpectation, *, timeout_ms: int
    ) -> FieldValueCheck:
        return await self._inner.wait_for_field_value(element, expected, timeout_ms=timeout_ms)

    async def screenshot(self, *, mask: Sequence[Selector], timeout_ms: int) -> bytes:
        return await self._inner.screenshot(mask=mask, timeout_ms=timeout_ms)

    async def dom_snapshot(self) -> str:
        return await self._inner.dom_snapshot()

    async def export_trace(self, *, scrubber: SecretScrubber) -> TraceExport:
        return await self._inner.export_trace(scrubber=scrubber)


@dataclass
class GroundTruthLauncher:
    """Opens ground-truth sessions, optionally signed in, and collects what the page recorded."""

    inner: PlaywrightLauncher
    targets: Mapping[str, str]
    tracker: StepTracker
    checks: list[ActionCheck]
    wrong_actions: list[str]
    signed_in: bool

    @asynccontextmanager
    async def session(
        self, run_id: RunId, egress: EgressPolicy
    ) -> AsyncIterator[GroundTruthSession]:
        async with self.inner.session(run_id, egress) as session:
            if self.signed_in:
                await session.page.add_init_script(script=_SIGNED_IN)
            try:
                yield GroundTruthSession(session, self.targets, self.tracker, self.checks)
            finally:
                try:
                    labels = _LABELS.validate_python(await session.page.evaluate(_WRONG_ACTIONS))
                except PlaywrightError as error:
                    labels = [f"wrong actions could not be read: {error.message.splitlines()[0]}"]
                self.wrong_actions.extend(labels)
