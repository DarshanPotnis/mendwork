"""Opening the browser a person records in: one context with the recorder installed.

The binding and the init script are registered on the context before its page exists, so
the recorder is in the very first document and in every document after it. Tracing is off:
a trace would snapshot what the person types. Downloads go to a temporary directory that is
removed with the session.
"""

import asyncio
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from playwright.async_api import Browser, Playwright
from playwright.async_api import Error as PlaywrightError

from mendwork.adapters.browser_playwright.errors import is_closed
from mendwork.adapters.browser_playwright.launcher import (
    LaunchOptions,
    SessionOptions,
    new_context,
    remove_workdir,
    start_chromium,
)
from mendwork.adapters.browser_playwright.observations import Observations
from mendwork.adapters.browser_playwright.recording.channel import (
    InboundObserver,
    RecorderChannel,
)
from mendwork.adapters.browser_playwright.recording.navigation_log import NavigationLog
from mendwork.adapters.browser_playwright.recording.session import PlaywrightRecordingSession
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.adapters.browser_playwright.tracing import TraceRecorder


@dataclass(frozen=True, slots=True)
class RecordingOptions:
    """How a recording's browser context is set up."""

    viewport_width: int
    viewport_height: int
    default_timeout_ms: int


class PlaywrightRecordingLauncher:
    """Opens recording sessions on a browser someone else launched and will close."""

    def __init__(
        self,
        browser: Browser,
        scripts: PageScripts,
        options: RecordingOptions,
        inbound: InboundObserver,
    ) -> None:
        self._browser = browser
        self._scripts = scripts
        self._options = options
        self._inbound = inbound

    @classmethod
    async def create(
        cls, browser: Browser, options: RecordingOptions, inbound: InboundObserver
    ) -> Self:
        """A launcher with its page scripts loaded off the event loop."""
        return cls(browser, await asyncio.to_thread(PageScripts.load), options, inbound)

    @asynccontextmanager
    async def recording_session(self) -> AsyncIterator[PlaywrightRecordingSession]:
        options = self._options
        workdir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="mendwork-recording-"))
        try:
            context = await new_context(
                self._browser,
                SessionOptions(
                    viewport_width=options.viewport_width,
                    viewport_height=options.viewport_height,
                    default_timeout_ms=options.default_timeout_ms,
                    trace_on_failure=False,
                ),
                proxy_server=None,
            )
            channel = RecorderChannel(self._inbound)
            try:
                context.set_default_timeout(options.default_timeout_ms)
                await channel.install(context, self._scripts.recorder)
                page = await context.new_page()
                channel.attach(page)
                page.on("close", lambda _: channel.close())
                navigation = NavigationLog(channel)
                await navigation.start(context, page)
                yield PlaywrightRecordingSession(
                    page=page,
                    scripts=self._scripts,
                    observations=Observations(page, context, workdir / "downloads"),
                    tracer=TraceRecorder(context, workdir, enabled=False),
                    channel=channel,
                    navigation=navigation,
                    load_timeout_ms=options.default_timeout_ms,
                )
            finally:
                channel.close()
                try:
                    await context.close()
                except PlaywrightError as error:
                    if not is_closed(error):
                        raise
        finally:
            await asyncio.to_thread(remove_workdir, workdir)


class ChromiumRecordingLauncher:
    """Launches Chromium, headed, when recording starts, and closes it when the launcher closes."""

    def __init__(
        self, launch: LaunchOptions, options: RecordingOptions, inbound: InboundObserver
    ) -> None:
        self._launch = launch
        self._options = options
        self._inbound = inbound
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._inner: PlaywrightRecordingLauncher | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    @asynccontextmanager
    async def recording_session(self) -> AsyncIterator[PlaywrightRecordingSession]:
        if self._inner is None:
            self._playwright, self._browser = await start_chromium(self._launch)
            self._inner = await PlaywrightRecordingLauncher.create(
                self._browser, self._options, self._inbound
            )
        async with self._inner.recording_session() as session:
            yield session
