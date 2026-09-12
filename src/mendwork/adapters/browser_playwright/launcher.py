"""Browsers and sessions: Chromium launched once, one isolated context per run.

Each run gets its own BrowserContext (no cookies or storage shared with any other run), its
own downloads directory, and its own trace, all removed when the run's session closes.
"""

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

import structlog
from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from mendwork.adapters.browser_playwright.errors import first_line, is_closed
from mendwork.adapters.browser_playwright.observations import Observations
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.adapters.browser_playwright.tracing import TraceRecorder
from mendwork.engine.domain.runs import RunId
from mendwork.engine.errors import BrowserUnavailable


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """How Chromium is started."""

    headless: bool
    slow_mo_ms: int


@dataclass(frozen=True, slots=True)
class SessionOptions:
    """How each run's browser context is set up."""

    viewport_width: int
    viewport_height: int
    default_timeout_ms: int
    trace_on_failure: bool


class PlaywrightLauncher:
    """Opens one context per run on a browser someone else launched and will close."""

    def __init__(self, browser: Browser, scripts: PageScripts, options: SessionOptions) -> None:
        self._browser = browser
        self._scripts = scripts
        self._options = options

    @classmethod
    async def create(cls, browser: Browser, options: SessionOptions) -> "PlaywrightLauncher":
        """A launcher with its page scripts loaded off the event loop."""
        return cls(browser, await asyncio.to_thread(PageScripts.load), options)

    @asynccontextmanager
    async def session(self, run_id: RunId) -> AsyncIterator[PlaywrightSession]:
        async with open_session(self._browser, self._scripts, self._options, run_id) as session:
            yield session


class ChromiumLauncher:
    """Launches Chromium on the first session and closes it when the launcher closes.

    Nothing starts until a run needs a browser, so a run refused for bad inputs or missing
    secrets never launches one.
    """

    def __init__(self, launch: LaunchOptions, options: SessionOptions) -> None:
        self._launch = launch
        self._options = options
        self._playwright: Playwright | None = None
        self._inner: PlaywrightLauncher | None = None
        self._browser: Browser | None = None

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
    async def session(self, run_id: RunId) -> AsyncIterator[PlaywrightSession]:
        launcher = await self._launched()
        async with launcher.session(run_id) as session:
            yield session

    async def _launched(self) -> PlaywrightLauncher:
        if self._inner is not None:
            return self._inner
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._launch.headless, slow_mo=self._launch.slow_mo_ms
            )
        except PlaywrightError as error:
            raise BrowserUnavailable(
                "could not launch Chromium; install it with `make install`",
                detail=first_line(error),
            ) from error
        self._inner = await PlaywrightLauncher.create(self._browser, self._options)
        return self._inner


@asynccontextmanager
async def open_session(
    browser: Browser, scripts: PageScripts, options: SessionOptions, run_id: RunId
) -> AsyncIterator[PlaywrightSession]:
    """One run's context, page, downloads directory, and trace; all removed afterwards."""
    workdir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix=f"mendwork-{run_id}-"))
    try:
        context = await _new_context(browser, options)
        tracer = TraceRecorder(context, workdir, enabled=options.trace_on_failure)
        try:
            context.set_default_timeout(options.default_timeout_ms)
            await tracer.start()
            page = await context.new_page()
            observations = Observations(page, context, workdir / "downloads")
            yield PlaywrightSession(
                page=page, scripts=scripts, observations=observations, tracer=tracer
            )
        finally:
            await tracer.stop()
            try:
                await context.close()
            except PlaywrightError as error:
                if not is_closed(error):
                    raise
    finally:
        await asyncio.to_thread(_remove, workdir)


async def _new_context(browser: Browser, options: SessionOptions) -> BrowserContext:
    try:
        return await browser.new_context(
            viewport={"width": options.viewport_width, "height": options.viewport_height},
            accept_downloads=True,
        )
    except PlaywrightError as error:
        raise BrowserUnavailable(
            "could not open a browser context", detail=first_line(error)
        ) from error


def _remove(workdir: Path) -> None:
    try:
        shutil.rmtree(workdir)
    except OSError as error:
        structlog.stdlib.get_logger("mendwork.browser").warning(
            "run_workdir_not_removed", path=str(workdir), errno=error.errno
        )
