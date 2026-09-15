"""The RecordingBrowser on one Playwright page: a replay session plus the recorder's handshake.

Everything a recording page script returns passes through the channel's observer before it
is parsed. The one read of a field's content is ``read_field_text``, which the engine calls
only for fields it has already decided are not credential fields.
"""

from typing import Final

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from mendwork.adapters.browser_playwright.egress.log import EgressLog
from mendwork.adapters.browser_playwright.errors import (
    browser_closed,
    is_closed,
    is_context_destroyed,
    page_read_error,
)
from mendwork.adapters.browser_playwright.facts import FACTS_REQUEST, FactsReply
from mendwork.adapters.browser_playwright.observations import Observations
from mendwork.adapters.browser_playwright.recording.channel import RecorderChannel
from mendwork.adapters.browser_playwright.recording.messages import (
    ControlReply,
    FieldTextReply,
)
from mendwork.adapters.browser_playwright.recording.navigation_log import NavigationLog
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.adapters.browser_playwright.session import PlaywrightSession, dispose_handle
from mendwork.adapters.browser_playwright.tracing import TraceRecorder
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.ports.browser_types import ElementRef, WatchId
from mendwork.engine.ports.element_types import ElementFacts
from mendwork.engine.ports.recording import StopSignal
from mendwork.engine.ports.recording_types import (
    CaptureRef,
    FieldText,
    Landmark,
    NavigationRecord,
    PageEvent,
    PageObservation,
)

LANDMARK_CANDIDATES: Final = (
    "css=h1, h2, h3, h4, h5, h6, [role=heading], main, nav, aside, dialog, search, "
    "section[aria-label], section[aria-labelledby], form[aria-label], form[aria-labelledby], "
    "[role=main], [role=navigation], [role=region], [role=complementary], [role=search], "
    "[role=form], [role=dialog], [role=alertdialog], [role=banner], [role=contentinfo]"
)
LIVE_REGIONS: Final = (
    "css=[role=status], [role=alert], [role=log], [aria-live]:not([aria-live=off]), output"
)
_OBSERVE_ATTEMPTS: Final = 3


class PlaywrightRecordingSession(PlaywrightSession):
    """One recording's page."""

    def __init__(
        self,
        *,
        page: Page,
        scripts: PageScripts,
        observations: Observations,
        tracer: TraceRecorder,
        channel: RecorderChannel,
        navigation: NavigationLog,
        load_timeout_ms: int,
    ) -> None:
        # A person drives a recording's browser, so it has no egress gateway and its log stays
        # empty; the recording's verification replay is held to the policy.
        super().__init__(
            page=page,
            scripts=scripts,
            observations=observations,
            tracer=tracer,
            egress=EgressLog(),
            replies=channel.observe,
        )
        self._channel = channel
        self._navigation = navigation
        self._load_timeout_ms = load_timeout_ms

    async def next_event(self, stop: StopSignal) -> PageEvent | None:
        return await self._channel.next_event(stop)

    async def flush_pending(self, *, timeout_ms: int) -> tuple[PageEvent, ...]:
        if not self._page.is_closed():
            reply = await self._control({"operation": "flush"})
            if reply is not None and reply.document is not None:
                await self._channel.wait_delivered(
                    reply.document, reply.sequence, timeout_ms=timeout_ms
                )
        return self._channel.drain()

    async def pin_capture(self, ref: CaptureRef) -> ElementRef | None:
        if ref.element is None:
            return None
        try:
            handle = await self._page.evaluate_handle(
                self._scripts.recorder_element, {"document": ref.document, "element": ref.element}
            )
        except PlaywrightError as error:
            if is_closed(error):
                raise browser_closed(error) from error
            if is_context_destroyed(error):
                return None
            raise page_read_error(error) from error
        element = handle.as_element()
        if element is None:
            await handle.dispose()
            return None
        return self._pin(element)

    async def arm(self, ref: CaptureRef) -> bool:
        reply = await self._control(
            {"operation": "arm", "document": ref.document, "element": ref.element}
        )
        return reply is not None and reply.ok

    async def disarm(self, ref: CaptureRef) -> None:
        await self._control({"operation": "disarm", "document": ref.document})

    async def finish_capture(self, ref: CaptureRef) -> None:
        self._channel.finish(ref)

    async def element_facts(self, element: ElementRef) -> ElementFacts:
        raw = await self._evaluate_on(
            element,
            self._scripts.element_facts,
            FACTS_REQUEST,
        )
        self._channel.observe("element_facts", raw)
        return FactsReply.model_validate(raw).facts()

    async def read_field_text(self, element: ElementRef) -> FieldText:
        raw = await self._evaluate_on(element, self._scripts.field_text, None)
        self._channel.observe("field_text", raw)
        return FieldTextReply.model_validate(raw).field_text()

    async def observe_page(self, *, limit: int) -> PageObservation:
        for attempt in range(1, _OBSERVE_ATTEMPTS + 1):
            try:
                return await self._observe(limit)
            except PlaywrightError as error:
                if is_closed(error):
                    raise browser_closed(error) from error
                if not is_context_destroyed(error) or attempt == _OBSERVE_ATTEMPTS:
                    raise page_read_error(error) from error
                # A navigation replaced the document mid-read; read the new one once loaded.
                await self._wait_for_load(self._load_timeout_ms)
        raise AssertionError("unreachable: the last attempt returns or raises")

    async def navigations_since(self, sequence: int) -> tuple[NavigationRecord, ...]:
        return self._navigation.since(sequence)

    async def downloads_started(self, watch: WatchId) -> int:
        return self._observations.started(watch)

    async def _observe(self, limit: int) -> PageObservation:
        navigation = self._navigation.sequence
        url = self._page.url
        title = await self._page.title()
        handles = (
            await self._page.locator(LANDMARK_CANDIDATES).filter(visible=True).element_handles()
        )
        landmarks: list[Landmark] = []
        try:
            for handle in handles[:limit]:
                identity = await self._identity(handle)
                if identity.role and identity.name:
                    landmarks.append(Landmark(role=identity.role, name=identity.name))
        finally:
            for handle in handles:
                await dispose_handle(handle)
        texts = await self._page.locator(LIVE_REGIONS).filter(visible=True).all_inner_texts()
        self._channel.observe("live_regions", texts)
        return PageObservation(
            url=url,
            title=title,
            navigation=navigation,
            landmarks=tuple(landmarks),
            live_texts=tuple(texts),
        )

    async def _control(self, request: dict[str, object]) -> ControlReply | None:
        try:
            raw = await self._page.evaluate(self._scripts.recorder_control, request)
        except PlaywrightError as error:
            if is_closed(error):
                raise browser_closed(error) from error
            if is_context_destroyed(error):
                return None
            raise page_read_error(error) from error
        self._channel.observe("recorder_control", raw)
        return ControlReply.model_validate(raw)

    async def _evaluate_on(self, element: ElementRef, script: str, argument: object) -> object:
        try:
            return await self._handle(element).evaluate(script, argument)
        except PlaywrightError as error:
            raise _detached(error) from error


def _detached(error: PlaywrightError) -> Exception:
    if is_closed(error):
        return browser_closed(error)
    return TargetNotFound(
        "the recorded element is no longer on the page", reason="detached_while_recording"
    )
