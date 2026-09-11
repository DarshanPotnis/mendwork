"""Domain selectors resolve in Chromium exactly as documented, scopes included."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page
from pydantic import TypeAdapter

from mendwork.adapters.browser_playwright.locators import build_locator, scope_locators
from mendwork.engine.domain.selectors import Selector

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

SELECTOR: TypeAdapter[Selector] = TypeAdapter(Selector)

PAGE = """
<main>
  <form aria-label="Sign in">
    <label for="email">Email address</label>
    <input id="email" data-testid="login-email" placeholder="you@example.test">
    <input id="notes" aria-label="Email addresses to copy">
    <button id="sign-in" type="submit">Sign in</button>
  </form>
  <table aria-label="Recent orders">
    <tbody>
      <tr><th scope="row">PO-1041</th><td><button id="view-1041">View</button></td></tr>
      <tr><th scope="row">PO-1042</th><td><button id="view-1042">View</button></td></tr>
    </tbody>
  </table>
  <section aria-label="Archive">
    <table aria-label="Archived orders">
      <tbody>
        <tr><th scope="row">PO-1042</th><td><button id="archived-1042">View</button></td></tr>
      </tbody>
    </table>
  </section>
  <p id="note">Sign in to continue</p>
</main>
"""


@pytest_asyncio.fixture(loop_scope="session")
async def page(browser: Browser) -> AsyncIterator[Page]:
    context = await browser.new_context()
    try:
        opened = await context.new_page()
        await opened.set_content(PAGE)
        yield opened
    finally:
        await context.close()


async def ids(page: Page, raw: dict[str, Any]) -> list[str]:
    locator = build_locator(page, SELECTOR.validate_python(raw))
    return [str(await element.get_attribute("id")) for element in await locator.element_handles()]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"strategy": "test_id", "value": "login-email"}, ["email"]),
        ({"strategy": "role_name", "role": "button", "name": "Sign in"}, ["sign-in"]),
        ({"strategy": "role_name", "role": "button", "name": "sign", "exact": False}, ["sign-in"]),
        ({"strategy": "label", "value": "Email address"}, ["email"]),
        ({"strategy": "label", "value": "email address", "exact": False}, ["email", "notes"]),
        ({"strategy": "placeholder", "value": "you@example.test"}, ["email"]),
        ({"strategy": "text", "value": "Sign in"}, ["sign-in"]),
        ({"strategy": "text", "value": "sign in", "exact": False}, ["sign-in", "note"]),
        ({"strategy": "css", "value": "tbody button"}, ["view-1041", "view-1042", "archived-1042"]),
    ],
)
async def test_each_strategy_resolves_as_documented(
    page: Page, raw: dict[str, Any], expected: list[str]
) -> None:
    assert await ids(page, raw) == expected


async def test_an_unscoped_repeated_control_is_ambiguous(page: Page) -> None:
    assert len(await ids(page, {"strategy": "role_name", "role": "button", "name": "View"})) == 3


async def test_one_scope_narrows_to_the_row_but_can_still_be_ambiguous(page: Page) -> None:
    raw = {
        "strategy": "role_name",
        "role": "button",
        "name": "View",
        "within": {"strategy": "role_name", "role": "row", "name": "PO-1042", "exact": False},
    }

    assert await ids(page, raw) == ["view-1042", "archived-1042"]


async def test_two_scopes_resolve_a_single_element_and_every_level_is_countable(page: Page) -> None:
    selector = SELECTOR.validate_python(
        {
            "strategy": "role_name",
            "role": "button",
            "name": "View",
            "within": {
                "strategy": "role_name",
                "role": "row",
                "name": "PO-1042",
                "exact": False,
                "within": {"strategy": "role_name", "role": "table", "name": "Recent orders"},
            },
        }
    )

    levels = scope_locators(page, selector)

    assert [await level.count() for level in levels] == [1, 1, 1]
    assert await levels[-1].get_attribute("id") == "view-1042"


async def test_css_is_never_reinterpreted_as_another_selector_engine(page: Page) -> None:
    assert await ids(page, {"strategy": "css", "value": "button"}) == [
        "sign-in",
        "view-1041",
        "view-1042",
        "archived-1042",
    ]
