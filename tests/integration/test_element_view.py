"""The screenshot a run report outlines a healed element on (ADR 0013).

A viewport-sized window around the element, wherever it sits on a long page, taken without
scrolling; password fields and the selectors a step masks are covered, so no typed value reaches
the picture.
"""

import asyncio
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import pytest
from playwright.async_api import Browser
from pydantic import TypeAdapter

from mendwork.adapters.browser_playwright.launcher import (
    EgressEnforcement,
    SessionOptions,
    open_session,
)
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.engine.domain.runs import parse_run_id
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.ports.browser_types import ElementRef
from mendwork.engine.ports.element_types import Box
from mendwork.engine.safety.egress import EgressPolicy
from tests.fakes.egress import FakeResolver

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

SELECTOR: Final[TypeAdapter[Selector]] = TypeAdapter(Selector)
OPTIONS: Final = SessionOptions(
    viewport_width=800, viewport_height=600, default_timeout_ms=5_000, trace_on_failure=False
)
TIMEOUT_MS: Final = 5_000
LONG_PAGE: Final = """
<body style="margin: 0">
  <main style="position: relative; height: 3000px">
    <button id="top" style="position: absolute; top: 10px; left: 10px; width: 80px; height: 30px">
      Top
    </button>
    <label for="code" style="position: absolute; top: 2560px; left: 20px">Access code</label>
    <input id="code" type="password" style="position: absolute; top: 2600px; left: 20px">
    <input id="note" aria-label="Note" style="position: absolute; top: 2650px; left: 20px">
    <button id="export" style="position: absolute; top: 2800px; left: 300px; width: 160px;
      height: 40px">Export</button>
  </main>
</body>
"""


@asynccontextmanager
async def session_on(browser: Browser, html: str) -> AsyncIterator[PlaywrightSession]:
    scripts = await asyncio.to_thread(PageScripts.load)
    run_id = parse_run_id("20260915T000000Z-0000beef")
    enforcement = EgressEnforcement(resolver=FakeResolver(), timeout_ms=TIMEOUT_MS)
    async with open_session(
        browser, scripts, OPTIONS, run_id, policy=EgressPolicy(), enforcement=enforcement
    ) as session:
        await session.page.set_content(html)
        yield session


def css(value: str) -> Selector:
    return SELECTOR.validate_python({"strategy": "css", "value": value})


async def pinned(session: PlaywrightSession, value: str) -> ElementRef:
    match = await session.resolve_unique(css(value))
    assert match.element is not None, match
    return match.element


def png_size(png: bytes) -> tuple[int, int]:
    width, height = struct.unpack(">II", png[16:24])
    return int(width), int(height)


async def test_an_element_low_on_a_long_page_is_in_a_viewport_window_kept_inside_the_document(
    browser: Browser,
) -> None:
    async with session_on(browser, LONG_PAGE) as session:
        element = await pinned(session, "#export")

        view = await session.element_view(element, mask=(), timeout_ms=TIMEOUT_MS)
        scrolled = await session.page.evaluate("() => window.scrollY")

    assert png_size(view.png) == (800, 600)
    assert view.box == Box(x=0.375, y=0.6667, width=0.2, height=0.0667)
    assert scrolled == 0


async def test_an_element_at_the_top_of_the_page_keeps_the_window_at_the_top(
    browser: Browser,
) -> None:
    async with session_on(browser, LONG_PAGE) as session:
        view = await session.element_view(
            await pinned(session, "#top"), mask=(), timeout_ms=TIMEOUT_MS
        )

    assert png_size(view.png) == (800, 600)
    assert view.box == Box(x=0.0125, y=0.0167, width=0.1, height=0.05)


async def test_password_fields_and_masked_selectors_never_show_what_was_typed(
    browser: Browser,
) -> None:
    async with session_on(browser, LONG_PAGE) as session:
        element = await pinned(session, "#export")

        async def views(passcode: str, note: str) -> tuple[bytes, bytes]:
            await session.page.fill("#code", passcode)
            await session.page.fill("#note", note)
            masked = await session.element_view(
                element, mask=(css("#note"),), timeout_ms=TIMEOUT_MS
            )
            plain = await session.element_view(element, mask=(), timeout_ms=TIMEOUT_MS)
            return masked.png, plain.png

        masked_first, plain_first = await views("a", "first note")
        masked_second, plain_second = await views("a much longer passcode", "another note entirely")

    assert masked_first == masked_second
    assert plain_first != plain_second


async def test_an_element_removed_from_the_page_is_not_found(browser: Browser) -> None:
    async with session_on(browser, LONG_PAGE) as session:
        element = await pinned(session, "#top")
        await session.page.locator("#top").evaluate("element => element.remove()")

        with pytest.raises(TargetNotFound):
            await session.element_view(element, mask=(), timeout_ms=TIMEOUT_MS)
