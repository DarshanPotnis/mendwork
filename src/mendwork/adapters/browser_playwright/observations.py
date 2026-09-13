"""Events a step observes: downloads, responses, and pages the action opened.

Listeners are registered synchronously, inside ``watch``, so they exist before the step's
action is dispatched: an event the action causes cannot fire before anyone is listening.
Responses are recorded as URL and status only, never bodies.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import BrowserContext, Download, Page, Response
from playwright.async_api import Error as PlaywrightError

from mendwork.adapters.browser_playwright.errors import browser_closed, is_closed
from mendwork.engine.domain.checkpoints import ResponseReceived
from mendwork.engine.errors import MendworkError
from mendwork.engine.ports.browser_types import (
    DownloadObservation,
    ResponseObservation,
    WatchId,
    WatchKind,
)


@dataclass
class _Watch:
    kinds: frozenset[WatchKind]
    downloads: asyncio.Queue[Download] = field(default_factory=asyncio.Queue)
    responses: list[ResponseObservation] = field(default_factory=list)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    on_download: Callable[[Download], None] | None = None
    on_response: Callable[[Response], None] | None = None


class Observations:
    """Watches for one page, and the extra pages its context opens."""

    def __init__(self, page: Page, context: BrowserContext, downloads_dir: Path) -> None:
        self._page = page
        self._downloads_dir = downloads_dir
        self._watches: dict[WatchId, _Watch] = {}
        self._opened: list[Page] = []
        self._counter = 0
        context.on("page", self._on_page)

    def _on_page(self, page: Page) -> None:
        if page is not self._page:
            self._opened.append(page)

    async def take_opened_pages(self) -> int:
        """How many pages opened since the last call; they are closed."""
        opened, self._opened = self._opened, []
        for page in opened:
            await page.close()
        return len(opened)

    def watch(self, kinds: frozenset[WatchKind]) -> WatchId:
        """Start recording; listeners are attached before this returns."""
        self._counter += 1
        watch_id = WatchId(f"watch-{self._counter}")
        watch = _Watch(kinds=kinds)

        def on_download(download: Download) -> None:
            watch.downloads.put_nowait(download)

        def on_response(response: Response) -> None:
            watch.responses.append(ResponseObservation(url=response.url, status=response.status))
            watch.arrived.set()

        if WatchKind.DOWNLOAD in kinds:
            watch.on_download = on_download
            self._page.on("download", on_download)
        if WatchKind.RESPONSE in kinds:
            watch.on_response = on_response
            self._page.on("response", on_response)
        self._watches[watch_id] = watch
        return watch_id

    def started(self, watch_id: WatchId) -> int:
        """How many downloads started since the watch began and are not yet taken."""
        return self._require(watch_id).downloads.qsize()

    def unwatch(self, watch_id: WatchId) -> None:
        """Detach a watch's listeners and forget what it recorded."""
        watch = self._watches.pop(watch_id, None)
        if watch is None:
            return
        if watch.on_download is not None:
            self._page.remove_listener("download", watch.on_download)
        if watch.on_response is not None:
            self._page.remove_listener("response", watch.on_response)

    async def next_download(self, watch_id: WatchId, timeout_ms: int) -> DownloadObservation | None:
        """The first download since the watch began, saved once complete, or None on timeout."""
        watch = self._require(watch_id)
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                download = await watch.downloads.get()
                failure = await download.failure()
                if failure is not None:
                    return DownloadObservation(
                        suggested_filename=download.suggested_filename, failure=failure
                    )
                self._counter += 1
                path = self._downloads_dir / f"download-{self._counter}"
                await download.save_as(path)
                return DownloadObservation(
                    suggested_filename=download.suggested_filename, path=path
                )
        except TimeoutError:
            return None
        except PlaywrightError as error:
            if is_closed(error):
                raise browser_closed(error) from error
            return DownloadObservation(suggested_filename="", failure=error.message.splitlines()[0])

    async def next_response(
        self, watch_id: WatchId, checkpoint: ResponseReceived, timeout_ms: int
    ) -> ResponseObservation | None:
        """The first response since the watch began that satisfies the checkpoint."""
        watch = self._require(watch_id)
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                while True:
                    for response in watch.responses:
                        in_range = checkpoint.status_min <= response.status <= checkpoint.status_max
                        if in_range and checkpoint.matches_url(response.url):
                            return response
                    watch.arrived.clear()
                    await watch.arrived.wait()
        except TimeoutError:
            return None

    def _require(self, watch_id: WatchId) -> _Watch:
        watch = self._watches.get(watch_id)
        if watch is None:
            raise MendworkError("an observation was requested from a watch that is not active")
        return watch
