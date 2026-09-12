"""The BrowserPort on one Playwright page: pinned elements, page scripts, and guarded tracing."""

import time
from collections.abc import Sequence
from secrets import token_hex
from typing import Final

import structlog
from playwright.async_api import ElementHandle, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict

from mendwork.adapters.browser_playwright.errors import (
    action_error,
    browser_closed,
    is_closed,
    is_context_destroyed,
    navigation_error,
    page_read_error,
)
from mendwork.adapters.browser_playwright.identity import identify, read_identity, same_node
from mendwork.adapters.browser_playwright.locators import build_locator
from mendwork.adapters.browser_playwright.observations import Observations
from mendwork.adapters.browser_playwright.resolution import resolve_unique
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.adapters.browser_playwright.tracing import TraceRecorder
from mendwork.engine.domain.checkpoints import ResponseReceived, UrlMatches
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import MendworkError
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
    ResponseObservation,
    SecretText,
    Settling,
    TraceExport,
    UniqueMatch,
    WatchId,
    WatchKind,
)
from mendwork.engine.safety.secret_scrub import SecretScrubber

_DETACHED: Final = Actionability(attached=False, visible=False, enabled=False, editable=False)
# A reading of an element whose document was replaced. It matches no fingerprint, and Rung 0's
# stability check discards the snapshot it came from, so it can never lead to an action.
_GONE: Final = ElementIdentity(tag="", name="", confirmed=False)
_PASSWORD_FIELDS: Final = "input[type=password]"  # noqa: S105 - a CSS selector, not a credential


class _PageStateReply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document: str
    mutations: int
    quiet: bool
    changed: bool


class _FieldState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: bool
    empty: bool


class PlaywrightSession:
    """One run's page, behind the BrowserPort."""

    def __init__(
        self,
        *,
        page: Page,
        scripts: PageScripts,
        observations: Observations,
        tracer: TraceRecorder,
    ) -> None:
        self._page = page
        self._scripts = scripts
        self._observations = observations
        self._tracer = tracer
        self._handles: dict[ElementRef, ElementHandle] = {}
        self._pinned = 0
        self._log = structlog.stdlib.get_logger("mendwork.browser")

    @property
    def page(self) -> Page:
        """The session's page, for tests that must look at the page the run left behind."""
        return self._page

    # Navigation.

    async def navigate(self, url: str, *, timeout_ms: int) -> NavigationOutcome:
        try:
            response = await self._page.goto(url, wait_until="load", timeout=timeout_ms)
        except PlaywrightError as error:
            raise navigation_error(error) from error
        await self._resume_tracing(timeout_ms)
        return NavigationOutcome(
            url=self._page.url, status=None if response is None else response.status
        )

    async def wait_for_url(self, checkpoint: UrlMatches, *, timeout_ms: int) -> bool:
        try:
            await self._page.wait_for_url(
                checkpoint.matches_url, wait_until="commit", timeout=timeout_ms
            )
        except PlaywrightTimeoutError:
            return False
        except PlaywrightError as error:
            raise page_read_error(error) from error
        return True

    async def current_url(self) -> str:
        return self._page.url

    async def take_opened_pages(self) -> int:
        return await self._observations.take_opened_pages()

    # Settling.

    async def wait_until_settled(self, *, quiet_frames: int, timeout_ms: int) -> Settling:
        deadline = time.monotonic() + timeout_ms / 1000
        await self._wait_for_load(timeout_ms)
        reply = await self._state(
            {
                "operation": "settle",
                "token": token_hex(8),
                "frames": quiet_frames,
                "timeoutMs": _left(deadline),
            },
            deadline,
        )
        return Settling(
            quiet=reply.quiet, epoch=DomEpoch(document=reply.document, mutations=reply.mutations)
        )

    async def dom_epoch(self, *, timeout_ms: int) -> DomEpoch:
        reply = await self._state(
            {"operation": "epoch", "token": token_hex(8)}, time.monotonic() + timeout_ms / 1000
        )
        return DomEpoch(document=reply.document, mutations=reply.mutations)

    async def wait_for_dom_change(self, since: DomEpoch, *, timeout_ms: int) -> bool:
        request = {
            "operation": "change",
            "token": token_hex(8),
            "document": since.document,
            "since": since.mutations,
            "timeoutMs": timeout_ms,
        }
        try:
            raw = await self._page.evaluate(self._scripts.page_state, request)
        except PlaywrightError as error:
            if not is_context_destroyed(error):
                raise page_read_error(error) from error
            # A navigation replaced the document while waiting: that is a change.
            await self._wait_for_load(timeout_ms)
            return True
        reply = _PageStateReply.model_validate(raw)
        await self._tracer.observe_document(reply.document)
        return reply.changed or reply.document != since.document

    # Rung 0 primitives.

    async def resolve_unique(self, selector: Selector) -> UniqueMatch:
        try:
            resolution = await resolve_unique(self._page, selector)
        except PlaywrightError as error:
            if is_context_destroyed(error):
                # The document changed mid-read; the stability check will discard this.
                return UniqueMatch(level_counts=(0,))
            raise page_read_error(error) from error
        if resolution.element is None:
            return UniqueMatch(level_counts=resolution.level_counts)
        return UniqueMatch(
            level_counts=resolution.level_counts, element=self._pin(resolution.element)
        )

    async def group_identical(self, elements: Sequence[ElementRef]) -> tuple[int, ...]:
        if not elements:
            return ()
        handles = [self._handle(element) for element in elements]
        try:
            return tuple(await same_node(self._page, self._scripts, handles))
        except PlaywrightError as error:
            if is_context_destroyed(error):
                return tuple(range(len(handles)))
            raise page_read_error(error) from error

    async def identify(self, element: ElementRef, *, confirm: bool) -> ElementIdentity:
        try:
            return await identify(self._page, self._handle(element), self._scripts, confirm=confirm)
        except PlaywrightError as error:
            if is_closed(error):
                raise browser_closed(error) from error
            return _GONE

    async def actionability(self, element: ElementRef) -> Actionability:
        handle = self._handle(element)
        try:
            raw = await read_identity(handle, self._scripts)
            if not raw.connected:
                return _DETACHED
            visible = await handle.is_visible()
            enabled = await handle.is_enabled()
        except PlaywrightError as error:
            if is_closed(error):
                raise browser_closed(error) from error
            return _DETACHED
        return Actionability(
            attached=True, visible=visible, enabled=enabled, editable=await self._editable(handle)
        )

    async def release(self, elements: Sequence[ElementRef]) -> None:
        for element in elements:
            handle = self._handles.pop(element, None)
            if handle is None:
                continue
            try:
                await handle.dispose()
            except PlaywrightError as error:
                # A handle whose page closed or whose document was replaced holds nothing.
                self._log.debug("element_already_released", error=error.message.splitlines()[0])

    # Actions.

    async def click(self, element: ElementRef, *, timeout_ms: int) -> None:
        handle = self._handle(element)
        await self._resume_tracing(timeout_ms)
        try:
            await handle.click(timeout=timeout_ms)
        except PlaywrightError as error:
            raise action_error(error, "click") from error

    async def fill(self, element: ElementRef, value: FillText, *, timeout_ms: int) -> None:
        handle = self._handle(element)
        if isinstance(value, SecretText):
            # Recording stops before the secret is typed, never after.
            epoch = await self.dom_epoch(timeout_ms=timeout_ms)
            await self._tracer.pause_for_secret(epoch.document)
            text = value.value.get_secret_value()
        else:
            await self._resume_tracing(timeout_ms)
            text = value.value
        try:
            await handle.fill(text, timeout=timeout_ms)
        except PlaywrightError as error:
            raise action_error(error, "fill") from error

    async def select_option(self, element: ElementRef, label: str, *, timeout_ms: int) -> None:
        handle = self._handle(element)
        await self._resume_tracing(timeout_ms)
        try:
            await handle.select_option(label=label, timeout=timeout_ms)
        except PlaywrightError as error:
            raise action_error(error, "select") from error

    async def press(self, element: ElementRef | None, key: str, *, timeout_ms: int) -> None:
        await self._resume_tracing(timeout_ms)
        try:
            if element is None:
                await self._page.keyboard.press(key)
            else:
                await self._handle(element).press(key, timeout=timeout_ms)
        except PlaywrightError as error:
            raise action_error(error, "press") from error

    # Observations.

    async def watch(self, kinds: frozenset[WatchKind]) -> WatchId:
        return self._observations.watch(kinds)

    async def next_download(self, watch: WatchId, *, timeout_ms: int) -> DownloadObservation | None:
        return await self._observations.next_download(watch, timeout_ms)

    async def next_response(
        self, watch: WatchId, checkpoint: ResponseReceived, *, timeout_ms: int
    ) -> ResponseObservation | None:
        return await self._observations.next_response(watch, checkpoint, timeout_ms)

    async def unwatch(self, watch: WatchId) -> None:
        self._observations.unwatch(watch)

    async def visible_text(self) -> str:
        try:
            return await self._page.locator("body").inner_text()
        except PlaywrightError as error:
            if is_context_destroyed(error):
                return ""
            raise page_read_error(error) from error

    async def visible_alert_texts(self) -> tuple[str, ...]:
        try:
            return tuple(
                await self._page.get_by_role("alert").filter(visible=True).all_inner_texts()
            )
        except PlaywrightError as error:
            raise page_read_error(error) from error

    async def count_visible(self, selector: Selector) -> int:
        try:
            return await build_locator(self._page, selector).filter(visible=True).count()
        except PlaywrightError as error:
            raise page_read_error(error) from error

    async def wait_for_field_value(
        self, element: ElementRef, expected: FieldExpectation, *, timeout_ms: int
    ) -> FieldValueCheck:
        request = {
            "element": self._handle(element),
            # A secret is never sent back into the page: only emptiness is checked.
            "expected": expected.value if isinstance(expected, EqualsText) else None,
            "timeoutMs": timeout_ms,
        }
        try:
            state = _FieldState.model_validate(
                await self._page.evaluate(self._scripts.field_value, request)
            )
        except PlaywrightError as error:
            if is_context_destroyed(error):
                return FieldValueCheck(matches=False, empty=True)
            raise page_read_error(error) from error
        return FieldValueCheck(matches=state.matches, empty=state.empty)

    # Evidence.

    async def screenshot(self, *, mask: Sequence[Selector], timeout_ms: int) -> bytes:
        locators = [build_locator(self._page, selector) for selector in mask]
        locators.append(self._page.locator(_PASSWORD_FIELDS))
        try:
            return await self._page.screenshot(
                type="png", animations="disabled", caret="hide", mask=locators, timeout=timeout_ms
            )
        except PlaywrightError as error:
            raise page_read_error(error) from error

    async def dom_snapshot(self) -> str:
        try:
            return await self._page.content()
        except PlaywrightError as error:
            raise page_read_error(error) from error

    async def export_trace(self, *, scrubber: SecretScrubber) -> TraceExport:
        await self._resume_tracing(1)
        return await self._tracer.export(scrubber)

    # Internals.

    def _pin(self, handle: ElementHandle) -> ElementRef:
        self._pinned += 1
        ref = ElementRef(f"element-{self._pinned}")
        self._handles[ref] = handle
        return ref

    def _handle(self, element: ElementRef) -> ElementHandle:
        handle = self._handles.get(element)
        if handle is None:
            raise MendworkError("an element was used after it was released", element=element)
        return handle

    async def _state(self, request: dict[str, object], deadline: float) -> _PageStateReply:
        try:
            raw = await self._page.evaluate(self._scripts.page_state, request)
        except PlaywrightError as error:
            if not is_context_destroyed(error):
                raise page_read_error(error) from error
            # A navigation replaced the document mid-read; read the new one once it loads.
            await self._wait_for_load(_left(deadline))
            try:
                raw = await self._page.evaluate(self._scripts.page_state, request)
            except PlaywrightError as retry_error:
                raise page_read_error(retry_error) from retry_error
        reply = _PageStateReply.model_validate(raw)
        await self._tracer.observe_document(reply.document)
        return reply

    async def _wait_for_load(self, timeout_ms: int) -> None:
        try:
            await self._page.wait_for_load_state("load", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            # A slow subresource can delay the load event indefinitely. Reading the page
            # anyway is safe: Rung 0 only decides on a snapshot the DOM did not change under.
            self._log.info("page_load_incomplete", timeout_ms=timeout_ms)
        except PlaywrightError as error:
            raise page_read_error(error) from error

    async def _resume_tracing(self, timeout_ms: int) -> None:
        if self._tracer.paused:
            await self.dom_epoch(timeout_ms=timeout_ms)

    async def _editable(self, handle: ElementHandle) -> bool:
        try:
            return await handle.is_editable()
        except PlaywrightError as error:
            if is_closed(error):
                raise browser_closed(error) from error
            # Playwright refuses the question for elements that can never be edited, such
            # as a button, which answers it.
            return False


def _left(deadline: float) -> int:
    return max(1, round((deadline - time.monotonic()) * 1000))
