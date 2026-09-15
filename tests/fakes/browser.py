"""A scripted BrowserPort for engine tests: a page described as data, with a call log.

Selectors map to what they find. Elements carry the identity the page would report and
the state an action needs. Effects run when an element is acted on, and page changes run
one at a time whenever the engine waits for the DOM to change. Every wait that would
otherwise block advances the fake timer by its timeout instead.
"""

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import SecretStr

from mendwork.engine.domain.checkpoints import ResponseReceived, UrlMatches
from mendwork.engine.domain.runs import RunId
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import MendworkError, TargetNotFound
from mendwork.engine.ports.browser_types import (
    Actionability,
    DomEpoch,
    DownloadObservation,
    ElementIdentity,
    ElementRef,
    EqualsText,
    FieldExpectation,
    FieldValueCheck,
    FillText,
    NavigationOutcome,
    PlainText,
    ResponseObservation,
    Settling,
    TraceDisabled,
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
from tests.fakes.timer import FakeTimer

Effect = Callable[["FakeBrowser"], None]


@dataclass
class FakeElement:
    """One element as the page would report it."""

    tag: str
    name: str
    role: str | None = None
    input_type: str | None = None
    confirmed: bool | None = True
    attached: bool = True
    visible: bool = True
    enabled: bool = True
    editable: bool = True
    value: str = ""
    on_action: Effect | None = None
    action_error: MendworkError | None = None

    def identity(self, *, confirm: bool) -> ElementIdentity:
        return ElementIdentity(
            tag=self.tag,
            input_type=self.input_type,
            role=self.role,
            name=self.name,
            confirmed=self.confirmed if confirm and self.role is not None else None,
        )


@dataclass
class FakeBrowser:
    """A page whose content, reactions, and changes are written by the test."""

    timer: FakeTimer
    elements: dict[str, FakeElement] = field(default_factory=dict)
    finds: dict[Selector, str | tuple[int, ...]] = field(default_factory=dict)
    """A selector's element key for a hit, or its level counts for a miss; absent is (0,)."""
    facts: dict[str, ElementFacts] = field(default_factory=dict)
    """The facts an element reports; an element without an entry reports only its tag."""
    candidates: list[str] = field(default_factory=list)
    """The element keys a candidate scan finds, in document order."""
    detached: set[str] = field(default_factory=set)
    """Elements whose facts can no longer be read."""
    scans: list[CandidateQuery] = field(default_factory=list)
    url: str = "https://portal.example.test/"
    text: str = ""
    alerts: tuple[str, ...] = ()
    html: str = "<html></html>"
    quiet: bool = True
    changes: list[Effect] = field(default_factory=list)
    """Page changes applied, one per wait for a DOM change."""
    unstable_reads: int = 0
    """How many upcoming epoch reads report a DOM that changed during evaluation."""
    navigations: list[NavigationOutcome | MendworkError] = field(default_factory=list)
    trace: TraceExport = field(default_factory=TraceDisabled)
    calls: list[str] = field(default_factory=list)
    typed: list[FillText] = field(default_factory=list)
    masks: list[tuple[Selector, ...]] = field(default_factory=list)
    released: list[ElementRef] = field(default_factory=list)
    opened_pages: int = 0
    egress_blocks: list[EgressBlock] = field(default_factory=list)
    """Refusals the next check reports, as the adapter's gateway or document filter would."""
    epoch: int = 0
    document: int = 0
    _refs: dict[ElementRef, str] = field(default_factory=dict)
    _watches: dict[WatchId, set[WatchKind]] = field(default_factory=dict)
    _downloads: dict[WatchId, list[DownloadObservation]] = field(default_factory=dict)
    _responses: dict[WatchId, list[ResponseObservation]] = field(default_factory=dict)

    # Test-side controls.

    def change(self, effect: Effect | None = None) -> None:
        """Mutate the DOM now, optionally through an effect."""
        if effect is not None:
            effect(self)
        self.epoch += 1

    def emit_download(self, download: DownloadObservation) -> None:
        for watch, kinds in self._watches.items():
            if WatchKind.DOWNLOAD in kinds:
                self._downloads[watch].append(download)

    def emit_response(self, response: ResponseObservation) -> None:
        for watch, kinds in self._watches.items():
            if WatchKind.RESPONSE in kinds:
                self._responses[watch].append(response)

    def calls_named(self, *names: str) -> list[str]:
        return [call for call in self.calls if call.split(":", 1)[0] in names]

    # BrowserPort.

    async def navigate(self, url: str, *, timeout_ms: int) -> NavigationOutcome:
        self.calls.append(f"navigate:{url}")
        outcome = (
            self.navigations.pop(0) if self.navigations else NavigationOutcome(url=url, status=200)
        )
        if isinstance(outcome, MendworkError):
            raise outcome
        self.url = outcome.url
        self.document += 1
        self.epoch += 1
        return outcome

    async def wait_for_url(self, checkpoint: UrlMatches, *, timeout_ms: int) -> bool:
        if checkpoint.matches_url(self.url):
            return True
        self.timer.advance_ms(timeout_ms)
        return False

    async def current_url(self) -> str:
        return self.url

    async def take_opened_pages(self) -> int:
        opened, self.opened_pages = self.opened_pages, 0
        return opened

    async def take_egress_blocks(self) -> tuple[EgressBlock, ...]:
        blocks, self.egress_blocks = tuple(self.egress_blocks), []
        return blocks

    async def wait_until_settled(self, *, quiet_frames: int, timeout_ms: int) -> Settling:
        self.calls.append("settle")
        if not self.quiet:
            self.timer.advance_ms(timeout_ms)
        return Settling(quiet=self.quiet, epoch=self._epoch())

    async def dom_epoch(self, *, timeout_ms: int) -> DomEpoch:
        if self.unstable_reads:
            self.unstable_reads -= 1
            self.epoch += 1
        return self._epoch()

    async def wait_for_dom_change(self, since: DomEpoch, *, timeout_ms: int) -> bool:
        self.calls.append("wait_for_dom_change")
        if self._epoch() != since:
            return True
        if self.changes:
            self.change(self.changes.pop(0))
            self.timer.advance_ms(1)
            return True
        self.timer.advance_ms(timeout_ms)
        return False

    async def resolve_unique(self, selector: Selector) -> UniqueMatch:
        found = self.finds.get(selector, (0,))
        if isinstance(found, tuple):
            return UniqueMatch(level_counts=found)
        depth = 1 + _scope_depth(selector)
        return UniqueMatch(level_counts=(1,) * depth, element=self.pin(found))

    def pin(self, key: str) -> ElementRef:
        """A new ref to the element with this key, as the adapter pins a found node."""
        ref = ElementRef(f"ref-{len(self._refs) + 1}")
        self._refs[ref] = key
        return ref

    def key_of(self, ref: ElementRef) -> str:
        """The element key a ref was pinned to."""
        return self._refs[ref]

    async def element_facts(self, element: ElementRef) -> ElementFacts:
        key = self._refs[element]
        if key in self.detached:
            raise TargetNotFound("the element is no longer on the page", reason="detached")
        found = self.facts.get(key)
        if found is not None:
            return found
        tag = self.elements[key].tag
        return ElementFacts(tag=tag, structural_path=f"main > {tag}")

    async def scan_candidates(self, query: CandidateQuery) -> CandidateScan:
        self.scans.append(query)
        pinned: list[LiveCandidate] = []
        for key in self.candidates[: query.limit]:
            ref = self.pin(key)
            pinned.append(
                LiveCandidate(
                    element=ref,
                    identity=self.elements[key].identity(confirm=False),
                    facts=await self.element_facts(ref),
                )
            )
        return CandidateScan(candidates=tuple(pinned), total=len(self.candidates))

    async def group_identical(self, elements: Sequence[ElementRef]) -> tuple[int, ...]:
        keys = [self._refs[element] for element in elements]
        return tuple(keys.index(key) for key in keys)

    async def identify(self, element: ElementRef, *, confirm: bool) -> ElementIdentity:
        return self._element(element).identity(confirm=confirm)

    async def actionability(self, element: ElementRef) -> Actionability:
        found = self._element(element)
        return Actionability(
            attached=found.attached,
            visible=found.visible,
            enabled=found.enabled,
            editable=found.editable,
        )

    async def release(self, elements: Sequence[ElementRef]) -> None:
        self.released.extend(elements)

    async def click(self, element: ElementRef, *, timeout_ms: int) -> None:
        self._act("click", element)

    async def fill(self, element: ElementRef, value: FillText, *, timeout_ms: int) -> None:
        self._act("fill", element)
        self.typed.append(value)
        found = self._element(element)
        found.value = (
            value.value if isinstance(value, PlainText) else value.value.get_secret_value()
        )

    async def select_option(self, element: ElementRef, label: str, *, timeout_ms: int) -> None:
        self._act(f"select={label}", element)

    async def press(self, element: ElementRef | None, key: str, *, timeout_ms: int) -> None:
        if element is None:
            self.calls.append(f"press:{key}")
        else:
            self._act(f"press={key}", element)

    async def watch(self, kinds: frozenset[WatchKind]) -> WatchId:
        watch = WatchId(f"watch-{len(self._watches) + 1}")
        self.calls.append(f"watch:{','.join(sorted(kinds))}")
        self._watches[watch] = set(kinds)
        self._downloads[watch] = []
        self._responses[watch] = []
        return watch

    async def next_download(self, watch: WatchId, *, timeout_ms: int) -> DownloadObservation | None:
        downloads = self._downloads.get(watch, [])
        if downloads:
            return downloads.pop(0)
        self.timer.advance_ms(timeout_ms)
        return None

    async def next_response(
        self, watch: WatchId, checkpoint: ResponseReceived, *, timeout_ms: int
    ) -> ResponseObservation | None:
        for response in self._responses.get(watch, []):
            in_range = checkpoint.status_min <= response.status <= checkpoint.status_max
            if checkpoint.matches_url(response.url) and in_range:
                return response
        self.timer.advance_ms(timeout_ms)
        return None

    async def unwatch(self, watch: WatchId) -> None:
        self.calls.append("unwatch")
        self._watches.pop(watch, None)

    async def visible_text(self) -> str:
        return self.text

    async def visible_alert_texts(self) -> tuple[str, ...]:
        return self.alerts

    async def count_visible(self, selector: Selector) -> int:
        found = self.finds.get(selector, (0,))
        return found[-1] if isinstance(found, tuple) else 1

    async def wait_for_field_value(
        self, element: ElementRef, expected: FieldExpectation, *, timeout_ms: int
    ) -> FieldValueCheck:
        value = self._element(element).value
        matches = value == expected.value if isinstance(expected, EqualsText) else bool(value)
        if not matches:
            self.timer.advance_ms(timeout_ms)
        return FieldValueCheck(matches=matches, empty=not value)

    async def screenshot(self, *, mask: Sequence[Selector], timeout_ms: int) -> bytes:
        self.calls.append("screenshot")
        self.masks.append(tuple(mask))
        return b"\x89PNG fake"

    async def dom_snapshot(self) -> str:
        self.calls.append("dom_snapshot")
        return self.html

    async def export_trace(self, *, scrubber: SecretScrubber) -> TraceExport:
        self.calls.append("export_trace")
        return self.trace

    # Internals.

    def _epoch(self) -> DomEpoch:
        return DomEpoch(document=f"doc-{self.document}", mutations=self.epoch)

    def _element(self, ref: ElementRef) -> FakeElement:
        return self.elements[self._refs[ref]]

    def _act(self, name: str, ref: ElementRef) -> None:
        key = self._refs[ref]
        element = self.elements[key]
        if element.action_error is not None:
            raise element.action_error
        self.calls.append(f"{name}:{key}")
        if element.on_action is not None:
            element.on_action(self)


def _scope_depth(selector: Selector) -> int:
    depth = 0
    scope = selector.within
    while scope is not None:
        depth += 1
        scope = scope.within
    return depth


@dataclass
class FakeLauncher:
    """Hands out one FakeBrowser per run, or fails to, as a real launcher might."""

    browser: FakeBrowser
    error: MendworkError | None = None
    sessions: list[RunId] = field(default_factory=list)
    policies: list[EgressPolicy] = field(default_factory=list)
    closed: list[RunId] = field(default_factory=list)

    @asynccontextmanager
    async def session(self, run_id: RunId, egress: EgressPolicy) -> AsyncIterator[FakeBrowser]:
        if self.error is not None:
            raise self.error
        self.sessions.append(run_id)
        self.policies.append(egress)
        try:
            yield self.browser
        finally:
            self.closed.append(run_id)


def download(filename: str, path: str = "/downloads/1") -> DownloadObservation:
    """A completed download observation."""
    return DownloadObservation(suggested_filename=filename, path=Path(path))


def secret(value: str) -> SecretStr:
    return SecretStr(value)
