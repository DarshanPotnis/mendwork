"""Browser fixtures: one server and one browser per session, a fresh context per test.

A session is one pytest process: the serial run, or one pytest-xdist worker, each with its own
server on an OS-assigned port and its own browser (ADR 0005, ADR 0012). Playwright's driver
connection belongs to the event loop that created it, so every fixture and test that touches the
browser runs on the session loop.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Playwright, async_playwright

from mendwork.apps.portal.server import PortalServer
from tests.integration.portal import PORTAL_ROOT, PortalDriver, portal_session


@pytest.fixture(scope="session")
def portal_url() -> Iterator[str]:
    with PortalServer(PORTAL_ROOT, host="127.0.0.1", port=0) as server:
        yield server.url


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def playwright_instance() -> AsyncIterator[Playwright]:
    async with async_playwright() as instance:
        yield instance


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def browser(playwright_instance: Playwright) -> AsyncIterator[Browser]:
    launched = await playwright_instance.chromium.launch()
    try:
        yield launched
    finally:
        await launched.close()


@pytest_asyncio.fixture(loop_scope="session")
async def portal(browser: Browser, portal_url: str) -> AsyncIterator[PortalDriver]:
    async with portal_session(browser, portal_url) as driver:
        yield driver
