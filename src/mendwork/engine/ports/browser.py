"""The BrowserPort: the primitives replay needs from a browser, and nothing more.

The engine decides; the adapter observes and acts. Which selector wins, whether an
identity drifted, what a checkpoint means, and how long to wait are all engine decisions
made from these primitives. The adapter pins elements, runs page scripts, listens for
events, and keeps secrets out of its own tooling.
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from mendwork.engine.domain.checkpoints import ResponseReceived, UrlMatches
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
from mendwork.engine.safety.secret_scrub import SecretScrubber


class BrowserLauncher(Protocol):
    """Opens one isolated browser session per run: its own cookies, storage, and downloads."""

    def session(self, run_id: RunId) -> AbstractAsyncContextManager["BrowserPort"]:
        """A session for one run, closed when the context exits."""
        ...


class BrowserPort(Protocol):
    """One run's page. Every timeout is in milliseconds and always at least 1."""

    async def navigate(self, url: str, *, timeout_ms: int) -> NavigationOutcome:
        """Load a URL and wait for its load event.

        Raises NavigationError with ``reason`` (a network error code, ``timeout``) and no
        retry of its own: retrying is the engine's decision.
        """
        ...

    async def wait_for_url(self, checkpoint: UrlMatches, *, timeout_ms: int) -> bool:
        """Whether the page's URL satisfies the checkpoint within the timeout."""
        ...

    async def current_url(self) -> str:
        """The page's URL now."""
        ...

    async def take_opened_pages(self) -> int:
        """How many tabs or windows opened since the last call; they are closed."""
        ...

    async def wait_until_settled(self, *, quiet_frames: int, timeout_ms: int) -> Settling:
        """Wait for the load event, then for ``quiet_frames`` animation frames with no DOM
        mutation, or the timeout, whichever comes first."""
        ...

    async def dom_epoch(self, *, timeout_ms: int) -> DomEpoch:
        """The page's current document and mutation count."""
        ...

    async def wait_for_dom_change(self, since: DomEpoch, *, timeout_ms: int) -> bool:
        """Whether the DOM changed, or a new document loaded, after ``since``."""
        ...

    async def resolve_unique(self, selector: Selector) -> UniqueMatch:
        """Count visible matches at every scope level and pin the element if each is 1."""
        ...

    async def group_identical(self, elements: Sequence[ElementRef]) -> tuple[int, ...]:
        """For each element, the position of the first element that is the same DOM node."""
        ...

    async def identify(self, element: ElementRef, *, confirm: bool) -> ElementIdentity:
        """The element's tag, type, role, and accessible name, never a field's value.

        With ``confirm``, Playwright's own role locator must agree with the computed role
        and name for ``confirmed`` to be True.
        """
        ...

    async def actionability(self, element: ElementRef) -> Actionability:
        """Whether the element is attached, visible, enabled, and editable."""
        ...

    async def release(self, elements: Sequence[ElementRef]) -> None:
        """Forget pinned elements that are no longer needed."""
        ...

    async def click(self, element: ElementRef, *, timeout_ms: int) -> None:
        """Click the pinned element.

        Raises TargetNotFound if it detached first, and TargetNotActionable if it never
        became clickable (covered, moving, disabled) within the timeout. Neither clicks.
        """
        ...

    async def fill(self, element: ElementRef, value: FillText, *, timeout_ms: int) -> None:
        """Replace the pinned field's content. A SecretText value is never traced."""
        ...

    async def select_option(self, element: ElementRef, label: str, *, timeout_ms: int) -> None:
        """Choose the option with this visible label in the pinned select."""
        ...

    async def press(self, element: ElementRef | None, key: str, *, timeout_ms: int) -> None:
        """Press a key on the pinned element, or on the focused page when there is none."""
        ...

    async def watch(self, kinds: frozenset[WatchKind]) -> WatchId:
        """Start recording downloads or responses. Listeners exist before this returns."""
        ...

    async def next_download(self, watch: WatchId, *, timeout_ms: int) -> DownloadObservation | None:
        """The first download since the watch began, once finished, or None on timeout."""
        ...

    async def next_response(
        self, watch: WatchId, checkpoint: ResponseReceived, *, timeout_ms: int
    ) -> ResponseObservation | None:
        """The first response since the watch began that satisfies the checkpoint."""
        ...

    async def unwatch(self, watch: WatchId) -> None:
        """Stop recording and drop what was recorded."""
        ...

    async def visible_text(self) -> str:
        """The page body's rendered text."""
        ...

    async def visible_alert_texts(self) -> tuple[str, ...]:
        """The rendered text of every visible ``role=alert`` element."""
        ...

    async def count_visible(self, selector: Selector) -> int:
        """How many visible elements a selector matches, without pinning any."""
        ...

    async def wait_for_field_value(
        self, element: ElementRef, expected: FieldExpectation, *, timeout_ms: int
    ) -> FieldValueCheck:
        """Wait for the pinned field's value to meet the expectation, comparing in the page."""
        ...

    async def screenshot(self, *, mask: Sequence[Selector], timeout_ms: int) -> bytes:
        """A PNG of the viewport with password fields and ``mask`` selectors blacked out."""
        ...

    async def dom_snapshot(self) -> str:
        """The serialized DOM. The caller scrubs it before storing."""
        ...

    async def export_trace(self, *, scrubber: SecretScrubber) -> TraceExport:
        """Save the trace recorded since the last secret-bearing page, if that is safe."""
        ...
