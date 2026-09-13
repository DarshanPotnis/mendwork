"""Main-frame navigations, and who started each one.

Chromium reports ``Page.frameRequestedNavigation`` for a navigation the page starts (a link,
a form, a script, a meta refresh) and not for one the browser starts (the address bar, back,
forward, reload). A committed document with no such request before it was started by the
browser. Measured with Playwright 1.62 and Chromium 151; see ADR 0008. Same-document
navigations (fragments, history.pushState) do not commit a document and are not recorded.
"""

from playwright.async_api import BrowserContext, CDPSession, Page

from mendwork.adapters.browser_playwright.recording.channel import RecorderChannel
from mendwork.engine.ports.recording_types import (
    NavigationCommitted,
    NavigationInitiator,
    NavigationRecord,
    PageRestored,
)

_CURRENT_TAB = "currentTab"
_CACHE_RESTORE = "BackForwardCacheRestore"


class NavigationLog:
    """Every document the main frame committed, in order, each also queued as an event."""

    def __init__(self, channel: RecorderChannel) -> None:
        self._channel = channel
        self._records: list[NavigationRecord] = []
        self._main_frame_id: str | None = None
        self._requested = False
        self._session: CDPSession | None = None

    async def start(self, context: BrowserContext, page: Page) -> None:
        """Start listening; call before the page loads anything."""
        session = await context.new_cdp_session(page)
        tree = await session.send("Page.getFrameTree")
        self._main_frame_id = _frame_id(tree.get("frameTree"))
        session.on("Page.frameRequestedNavigation", self._on_requested)
        session.on("Page.frameNavigated", self._on_navigated)
        await session.send("Page.enable")
        self._session = session

    @property
    def sequence(self) -> int:
        """How many documents have committed."""
        return len(self._records)

    def since(self, sequence: int) -> tuple[NavigationRecord, ...]:
        """The documents committed after the given sequence number."""
        return tuple(record for record in self._records if record.sequence > sequence)

    def _on_requested(self, params: dict[str, object]) -> None:
        if (
            params.get("frameId") == self._main_frame_id
            and params.get("disposition") == _CURRENT_TAB
        ):
            self._requested = True

    def _on_navigated(self, params: dict[str, object]) -> None:
        frame = params.get("frame")
        if not isinstance(frame, dict) or "parentId" in frame:
            return
        self._main_frame_id = _frame_id({"frame": frame})
        if params.get("type") == _CACHE_RESTORE:
            self._channel.put(PageRestored())
        url = f"{frame.get('url', '')}{frame.get('urlFragment', '')}"
        initiator = NavigationInitiator.PAGE if self._requested else NavigationInitiator.BROWSER
        self._requested = False
        record = NavigationRecord(sequence=len(self._records) + 1, url=url, initiator=initiator)
        self._records.append(record)
        self._channel.put(NavigationCommitted(record=record))


def _frame_id(tree: object) -> str | None:
    if not isinstance(tree, dict):
        return None
    frame = tree.get("frame")
    if not isinstance(frame, dict):
        return None
    identifier = frame.get("id")
    return identifier if isinstance(identifier, str) else None
