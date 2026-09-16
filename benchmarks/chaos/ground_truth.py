"""Ground truth at the moment of action: is the element about to be acted on the real target?

Benchmark and test code may read ``window.__chaos``; Mendwork never does. ``GroundTruthSession``
delegates every BrowserPort call to the real Playwright session. Before each action it asks the
page which of the workflow's controls the pinned element is, through ``window.__chaos.locate``, and
notes how many run events had been emitted, so the benchmark can line the action up with the run's
decisions (``mendwork.engine.benchmark.observe``). Around each action it reads how many wrong
actions the page recorded. When the run ends, before the browser context closes, it reads whether
the stopped step's real control was still attached and visible, and the page's recorded wrong
actions.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Final

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import JSHandle, Page
from pydantic import TypeAdapter

from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.engine.domain.checkpoints import ResponseReceived, UrlMatches
from mendwork.engine.domain.events import RunEvent, StepFailedEvent, StepStartedEvent
from mendwork.engine.domain.runs import RunId
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.ports.browser_types import (
    Actionability,
    DomEpoch,
    DownloadObservation,
    ElementIdentity,
    ElementRef,
    ElementView,
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
from mendwork.engine.ports.recording_types import AncestorFacts
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.egress_blocks import EgressBlock
from mendwork.engine.safety.secret_scrub import SecretScrubber

DEMO_EMAIL: Final = "buyer@harborline.test"
MATCHED_KEYS_SCRIPT: Final = """([keys, element]) => {
  const chaos = window.__chaos;
  if (chaos === undefined) {
    return [];
  }
  return keys.filter((key) => {
    if (chaos.pageId !== key.split(".")[0]) {
      return false;
    }
    try {
      return chaos.locate(key) === element;
    } catch {
      return false;
    }
  });
}"""
AVAILABLE_SCRIPT: Final = """(key) => {
  const chaos = window.__chaos;
  if (chaos === undefined || chaos.pageId !== key.split(".")[0]) {
    return false;
  }
  let element = null;
  try {
    element = chaos.locate(key);
  } catch {
    return false;
  }
  if (element === null || !element.isConnected) {
    return false;
  }
  const box = element.getBoundingClientRect();
  return box.width > 0 && box.height > 0 && getComputedStyle(element).visibility === "visible";
}"""
WRONG_COUNT_SCRIPT: Final = """() =>
  window.__chaos === undefined ? 0 : window.__chaos.wrongActions.length"""
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
SIGNED_IN_SCRIPT: Final = (
    "sessionStorage.setItem('harborline.session', JSON.stringify({ email: '" + DEMO_EMAIL + "' }));"
)
_LABELS: Final[TypeAdapter[list[str]]] = TypeAdapter(list[str])
_KEYS: Final[TypeAdapter[list[str]]] = TypeAdapter(list[str])

MatchedKeys = Callable[[Page, Sequence[str], JSHandle], Awaitable[tuple[str, ...]]]
"""Which of the given controls an element really is, by ground truth."""
TargetAvailable = Callable[[Page, str], Awaitable[bool]]
"""Whether a control is on the page and could be acted on."""
WrongActionCount = Callable[[Page], Awaitable[int | None]]
"""How many wrong actions the page itself has recorded, where a page records them."""
TargetIndexOf = Callable[[Page, str, Sequence[JSHandle]], Awaitable[int]]
"""Which of a scan's candidates is the control, by ground truth; -1 when none of them is."""
TargetKnown = Callable[[Page, str], Awaitable[bool]]
"""Whether ground truth can name a control at this moment, so an action on it can be judged."""


@dataclass(frozen=True, slots=True)
class ActionCheck:
    """One action, the step that performed it, and whether it reached the real target."""

    step_id: str | None
    action: str
    target_key: str | None
    correct: bool | None
    """None when the step has no ground-truth target (a navigate step, say)."""
    event_position: int = 0
    """How many run events had been emitted when the action was about to be sent."""
    matched_keys: tuple[str, ...] = ()
    """Every control of the workflow the element was, by ground truth, at that moment."""
    ground_truth_known: bool = True
    """Whether ground truth could name the step's control then; False leaves the action unjudged."""


@dataclass
class StepTracker:
    """An EventSink that keeps every event, knows which step is running, and, for benchmark models,
    the element that really is each step's target at its latest candidate scan."""

    events: list[RunEvent] = field(default_factory=list)
    current: str | None = None
    truths: dict[str, LiveCandidate | None] = field(default_factory=dict)
    scans: dict[str, tuple[LiveCandidate, ...]] = field(default_factory=dict)
    page_wrong: dict[str, int] = field(default_factory=dict)
    """Wrong actions the page recorded while each step's actions were sent."""
    available: dict[str, bool] = field(default_factory=dict)
    """For the step a run stopped at: whether its real control was attached and visible."""

    async def emit(self, event: RunEvent) -> None:
        self.events.append(event)
        if isinstance(event, StepStartedEvent):
            self.current = event.step_id


async def matched_keys(page: Page, keys: Sequence[str], element: JSHandle) -> tuple[str, ...]:
    """The controls, among ``keys``, that ``element`` really is on the current page."""
    return tuple(_KEYS.validate_python(await page.evaluate(MATCHED_KEYS_SCRIPT, [keys, element])))


async def ground_truth_known(page: Page, key: str) -> bool:
    """On the chaos portal ground truth always answers: the page itself names every control."""
    return True


async def target_index(page: Page, key: str, handles: Sequence[JSHandle]) -> int:
    """Which handle is the control ``key``, by the portal's ground truth; -1 when none is."""
    index = await page.evaluate(_TARGET_INDEX, [key, list(handles)])
    return index if isinstance(index, int) else -1


async def wrong_action_count(page: Page) -> int | None:
    """How many wrong actions the current page recorded; None when the page could not be read."""
    try:
        count = await page.evaluate(WRONG_COUNT_SCRIPT)
    except PlaywrightError:
        return None
    return count if isinstance(count, int) else None


async def target_available(page: Page, key: str) -> bool:
    """Whether the control ``key`` names is attached and visible on the current page."""
    try:
        return bool(await page.evaluate(AVAILABLE_SCRIPT, key))
    except PlaywrightError:
        return False


class GroundTruthSession:
    """A BrowserPort that checks every action against the chaos ground truth first."""

    def __init__(
        self,
        inner: PlaywrightSession,
        targets: Mapping[str, str],
        tracker: StepTracker,
        checks: list[ActionCheck],
        *,
        matched: MatchedKeys = matched_keys,
        wrong_count: WrongActionCount = wrong_action_count,
        target_index: TargetIndexOf = target_index,
        known: TargetKnown = ground_truth_known,
    ) -> None:
        self._inner = inner
        self._targets = targets
        self._keys = sorted(set(targets.values()))
        self._tracker = tracker
        self._checks = checks
        self._matched = matched
        self._wrong_count = wrong_count
        self._target_index = target_index
        self._known = known

    async def _check(self, action: str, element: ElementRef) -> None:
        step_id = self._tracker.current
        key = self._targets.get(step_id) if step_id is not None else None
        handle = self._inner.pinned_handle(element)
        found = await self._matched(self._inner.page, self._keys, handle)
        known = True if key is None else await self._known(self._inner.page, key)
        self._checks.append(
            ActionCheck(
                step_id,
                action,
                key,
                None if key is None else key in found,
                event_position=len(self._tracker.events),
                matched_keys=found,
                ground_truth_known=known,
            )
        )

    async def _count_wrong(self, before: int | None) -> None:
        after = await self._wrong_count(self._inner.page)
        step_id = self._tracker.current
        if before is None or after is None or after <= before or step_id is None:
            return
        self._tracker.page_wrong[step_id] = (
            self._tracker.page_wrong.get(step_id, 0) + after - before
        )

    async def click(self, element: ElementRef, *, timeout_ms: int) -> None:
        await self._check("click", element)
        before = await self._wrong_count(self._inner.page)
        await self._inner.click(element, timeout_ms=timeout_ms)
        await self._count_wrong(before)

    async def fill(self, element: ElementRef, value: FillText, *, timeout_ms: int) -> None:
        await self._check("fill", element)
        before = await self._wrong_count(self._inner.page)
        await self._inner.fill(element, value, timeout_ms=timeout_ms)
        await self._count_wrong(before)

    async def select_option(self, element: ElementRef, label: str, *, timeout_ms: int) -> None:
        await self._check("select", element)
        before = await self._wrong_count(self._inner.page)
        await self._inner.select_option(element, label, timeout_ms=timeout_ms)
        await self._count_wrong(before)

    async def press(self, element: ElementRef | None, key: str, *, timeout_ms: int) -> None:
        if element is not None:
            await self._check("press", element)
        before = await self._wrong_count(self._inner.page)
        await self._inner.press(element, key, timeout_ms=timeout_ms)
        await self._count_wrong(before)

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
            index = await self._target_index(self._inner.page, key, handles)
            found = scan.candidates[index] if 0 <= index < len(scan.candidates) else None
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

    async def element_view(
        self, element: ElementRef, *, mask: Sequence[Selector], timeout_ms: int
    ) -> ElementView:
        return await self._inner.element_view(element, mask=mask, timeout_ms=timeout_ms)

    async def scope_ancestors(
        self, element: ElementRef, *, limit: int
    ) -> tuple[AncestorFacts, ...]:
        return await self._inner.scope_ancestors(element, limit=limit)

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
    matched: MatchedKeys = matched_keys
    available: TargetAvailable = target_available
    wrong_count: WrongActionCount = wrong_action_count
    target_index: TargetIndexOf = target_index
    known: TargetKnown = ground_truth_known

    @asynccontextmanager
    async def session(
        self, run_id: RunId, egress: EgressPolicy
    ) -> AsyncIterator[GroundTruthSession]:
        async with self.inner.session(run_id, egress) as session:
            if self.signed_in:
                await session.page.add_init_script(script=SIGNED_IN_SCRIPT)
            try:
                yield GroundTruthSession(
                    session,
                    self.targets,
                    self.tracker,
                    self.checks,
                    matched=self.matched,
                    wrong_count=self.wrong_count,
                    target_index=self.target_index,
                    known=self.known,
                )
            finally:
                await self._read_stop(session.page)
                try:
                    labels = _LABELS.validate_python(await session.page.evaluate(_WRONG_ACTIONS))
                except PlaywrightError as error:
                    labels = [f"wrong actions could not be read: {error.message.splitlines()[0]}"]
                self.wrong_actions.extend(labels)

    async def _read_stop(self, page: Page) -> None:
        """Whether the real control of the step the run stopped at was still there to act on."""
        failed = [event for event in self.tracker.events if isinstance(event, StepFailedEvent)]
        if not failed:
            return
        step_id = failed[-1].step_id
        key = self.targets.get(step_id)
        if key is not None:
            self.tracker.available[step_id] = await self.available(page, key)
