"""Browsers and sessions: Chromium launched once, one isolated context per run.

Each run gets its own BrowserContext (no cookies or storage shared with any other run), its own
downloads directory and trace, and its own egress enforcement: a SOCKS5 gateway that every
connection goes through, and a document filter on its page, both holding the run to its egress
policy (ADR 0011). All of it is removed when the run's session closes.
"""

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Self

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    ProxySettings,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

from mendwork.adapters.browser_playwright.egress.documents import DocumentFilter
from mendwork.adapters.browser_playwright.egress.gateway import EgressGateway
from mendwork.adapters.browser_playwright.egress.log import EgressLog
from mendwork.adapters.browser_playwright.errors import first_line, is_closed
from mendwork.adapters.browser_playwright.observations import Observations
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.adapters.browser_playwright.tracing import TraceRecorder
from mendwork.engine.domain.runs import RunId
from mendwork.engine.errors import BrowserUnavailable
from mendwork.engine.ports.resolver import HostResolver
from mendwork.engine.safety.egress import EgressPolicy

WEBRTC_PROXIED_UDP_ONLY: Final = "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
"""WebRTC sends UDP around a proxy unless told not to, and the gateway sees only what is proxied."""
_PROXY_BYPASS: Final = "<-loopback>"
"""Chromium sends loopback traffic around a proxy by default; this sends it through the gateway."""


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


@dataclass(frozen=True, slots=True)
class EgressEnforcement:
    """What every session needs to hold its run to an egress policy."""

    resolver: HostResolver
    timeout_ms: int
    """Bounds each connection's handshake, its name lookup, and each upstream connect attempt."""


class PlaywrightLauncher:
    """Opens one context per run on a browser someone else launched and will close."""

    def __init__(
        self,
        browser: Browser,
        scripts: PageScripts,
        options: SessionOptions,
        enforcement: EgressEnforcement,
    ) -> None:
        self._browser = browser
        self._scripts = scripts
        self._options = options
        self._enforcement = enforcement

    @classmethod
    async def create(
        cls, browser: Browser, options: SessionOptions, enforcement: EgressEnforcement
    ) -> "PlaywrightLauncher":
        """A launcher with its page scripts loaded off the event loop."""
        return cls(browser, await asyncio.to_thread(PageScripts.load), options, enforcement)

    @asynccontextmanager
    async def session(
        self, run_id: RunId, egress: EgressPolicy
    ) -> AsyncIterator[PlaywrightSession]:
        async with open_session(
            self._browser,
            self._scripts,
            self._options,
            run_id,
            policy=egress,
            enforcement=self._enforcement,
        ) as session:
            yield session


class ChromiumLauncher:
    """Launches Chromium on the first session and closes it when the launcher closes.

    Nothing starts until a run needs a browser, so a run refused for bad inputs or missing
    secrets never launches one.
    """

    def __init__(
        self, launch: LaunchOptions, options: SessionOptions, enforcement: EgressEnforcement
    ) -> None:
        self._launch = launch
        self._options = options
        self._enforcement = enforcement
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
    async def session(
        self, run_id: RunId, egress: EgressPolicy
    ) -> AsyncIterator[PlaywrightSession]:
        launcher = await self._launched()
        async with launcher.session(run_id, egress) as session:
            yield session

    async def _launched(self) -> PlaywrightLauncher:
        if self._inner is not None:
            return self._inner
        self._playwright, self._browser = await start_chromium(self._launch)
        self._inner = await PlaywrightLauncher.create(
            self._browser, self._options, self._enforcement
        )
        return self._inner


async def start_chromium(launch: LaunchOptions) -> tuple[Playwright, Browser]:
    """Start Playwright and launch Chromium, or raise BrowserUnavailable."""
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch(
            headless=launch.headless, slow_mo=launch.slow_mo_ms, args=[WEBRTC_PROXIED_UDP_ONLY]
        )
    except PlaywrightError as error:
        await playwright.stop()
        raise BrowserUnavailable(
            "could not launch Chromium; install it with `make install`",
            detail=first_line(error),
        ) from error
    return playwright, browser


@asynccontextmanager
async def open_session(
    browser: Browser,
    scripts: PageScripts,
    options: SessionOptions,
    run_id: RunId,
    *,
    policy: EgressPolicy,
    enforcement: EgressEnforcement,
) -> AsyncIterator[PlaywrightSession]:
    """One run's context, page, downloads directory, trace, and egress enforcement.

    The gateway starts before the context exists and stops after it closes, so the context
    never has a moment without it; all of it is removed afterwards.
    """
    workdir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix=f"mendwork-{run_id}-"))
    log = EgressLog()
    try:
        async with EgressGateway(
            policy=policy, resolver=enforcement.resolver, log=log, timeout_ms=enforcement.timeout_ms
        ) as gateway:
            context = await new_context(browser, options, proxy_server=gateway.proxy_server)
            tracer = TraceRecorder(context, workdir, enabled=options.trace_on_failure)
            documents = DocumentFilter(policy=policy, log=log)
            try:
                context.set_default_timeout(options.default_timeout_ms)
                await tracer.start()
                page = await context.new_page()
                await documents.attach(context, page)
                observations = Observations(page, context, workdir / "downloads")
                yield PlaywrightSession(
                    page=page,
                    scripts=scripts,
                    observations=observations,
                    tracer=tracer,
                    egress=log,
                )
            finally:
                await documents.detach()
                await tracer.stop()
                try:
                    await context.close()
                except PlaywrightError as error:
                    if not is_closed(error):
                        raise
    finally:
        await asyncio.to_thread(remove_workdir, workdir)


async def new_context(
    browser: Browser, options: SessionOptions, *, proxy_server: str | None
) -> BrowserContext:
    """A fresh context: its own cookies and storage, the configured viewport, downloads on.

    Service workers are blocked, because a service worker answers requests the egress filter
    would never see. A run's context sends every connection through its egress gateway; a
    recording's (``proxy_server`` None) does not, because a person drives it.
    """
    proxy: ProxySettings | None = (
        None if proxy_server is None else {"server": proxy_server, "bypass": _PROXY_BYPASS}
    )
    try:
        return await browser.new_context(
            viewport={"width": options.viewport_width, "height": options.viewport_height},
            accept_downloads=True,
            proxy=proxy,
            service_workers="block",
        )
    except PlaywrightError as error:
        raise BrowserUnavailable(
            "could not open a browser context", detail=first_line(error)
        ) from error


def remove_workdir(workdir: Path) -> None:
    """Delete a session's temporary directory, logging rather than failing if it cannot."""
    try:
        shutil.rmtree(workdir)
    except OSError as error:
        structlog.stdlib.get_logger("mendwork.browser").warning(
            "run_workdir_not_removed", path=str(workdir), errno=error.errno
        )
