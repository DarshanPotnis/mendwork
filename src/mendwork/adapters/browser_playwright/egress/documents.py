"""The document filter: every document request checked against the egress policy, hops included.

Through the DevTools Protocol's Fetch domain, the run's page pauses every document request before
it is sent: navigations, frames, and each hop of a redirect chain (measured: each hop is paused
with ``redirectedRequestId`` set, and a failed request is never sent). The page's own requests
must pass the scheme, URL-shape, address, and allowlist rules; a frame's must pass all but the
allowlist. Host names are not resolved here: the gateway resolves each name once, when the browser
connects, and refuses internal addresses there.

Pages the run did not open (popups) are not attached. Their connections still pass through the
gateway, and the step that opened them fails.
"""

import asyncio
from typing import Any, Final

import structlog
from playwright.async_api import BrowserContext, CDPSession, Page
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mendwork.adapters.browser_playwright.egress.log import EgressLog
from mendwork.adapters.browser_playwright.errors import first_line, is_closed
from mendwork.engine.errors import BrowserUnavailable
from mendwork.engine.safety.egress import EgressPolicy, check_navigation
from mendwork.engine.safety.egress_blocks import EgressBlock, EgressLayer

_DOCUMENTS: Final = {
    "patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}]
}
_BLOCKED_BY_CLIENT: Final = "BlockedByClient"


class _Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class _Request(_Message):
    url: str


class _Paused(_Message):
    request_id: str = Field(alias="requestId")
    request: _Request
    frame_id: str | None = Field(default=None, alias="frameId")


class _Frame(_Message):
    id: str


class _FrameNode(_Message):
    frame: _Frame


class _FrameTree(_Message):
    frame_tree: _FrameNode = Field(alias="frameTree")


class DocumentFilter:
    """Decides every document request of one page against one run's policy."""

    def __init__(self, *, policy: EgressPolicy, log: EgressLog) -> None:
        self._policy = policy
        self._log = log
        self._session: CDPSession | None = None
        self._main_frame: str | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._logger = structlog.stdlib.get_logger("mendwork.egress")

    async def attach(self, context: BrowserContext, page: Page) -> None:
        """Start pausing the page's document requests; raises BrowserUnavailable if it cannot."""
        try:
            session = await context.new_cdp_session(page)
            tree = _FrameTree.model_validate(await session.send("Page.getFrameTree"))
            session.on("Fetch.requestPaused", self._paused)
            await session.send("Fetch.enable", _DOCUMENTS)
        except PlaywrightError as error:
            raise BrowserUnavailable(
                "could not start the egress document filter", detail=first_line(error)
            ) from error
        self._session = session
        self._main_frame = tree.frame_tree.frame.id

    async def detach(self) -> None:
        """Stop deciding; requests still paused are abandoned with the page."""
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        session, self._session = self._session, None
        if session is None:
            return
        try:
            await session.detach()
        except PlaywrightError as error:
            self._logger.debug("egress_filter_already_detached", error=first_line(error))

    # Any: DevTools events arrive as untyped JSON, and are validated before anything reads them.
    def _paused(self, event: Any) -> None:  # noqa: ANN401
        task = asyncio.ensure_future(self._decide(event))
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self._logger.error(
                "egress_document_decision_failed", error_type=type(task.exception()).__name__
            )

    async def _decide(self, event: object) -> None:
        session = self._session
        if session is None:
            return
        try:
            paused = _Paused.model_validate(event)
        except ValidationError:
            # An unreadable request is left paused: its navigation times out rather than proceed.
            self._logger.warning("egress_document_unreadable")
            return
        main_frame = paused.frame_id == self._main_frame
        refusal = check_navigation(paused.request.url, self._policy, main_frame=main_frame)
        try:
            if refusal is None:
                await session.send("Fetch.continueRequest", {"requestId": paused.request_id})
                return
            self._log.block(
                EgressBlock(layer=EgressLayer.DOCUMENT, main_frame=main_frame, refusal=refusal)
            )
            self._logger.warning(
                "egress_document_refused",
                host=refusal.host,
                rule=refusal.rule.value,
                main_frame=main_frame,
            )
            await session.send(
                "Fetch.failRequest",
                {"requestId": paused.request_id, "errorReason": _BLOCKED_BY_CLIENT},
            )
        except PlaywrightError as error:
            level = self._logger.debug if is_closed(error) else self._logger.warning
            level("egress_document_decision_not_delivered", error=first_line(error))
