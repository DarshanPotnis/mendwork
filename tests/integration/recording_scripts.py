"""Scripted people doing the example workflows' tasks on the chaos portal.

Each action is an ordinary Playwright call a person's click or typing stands for, and each
waits for the recorder's own notice before the next one, so no step races the one before.
"""

from typing import Final

from mendwork.adapters.browser_playwright.locators import build_locator
from mendwork.engine.domain.selectors import Selector
from tests.integration.portal import DEMO_EMAIL, DEMO_PASSWORD
from tests.integration.recording_harness import ScriptedUser

UNSCOPED_VIEW: Final[dict[str, object]] = {
    "strategy": "role_name",
    "role": "button",
    "name": "View",
    "exact": False,
}


async def sign_in(user: ScriptedUser) -> None:
    """Steps 2 to 4: the email, the password, and the Sign in button."""
    page = user.page
    await page.get_by_label("Email address").fill(DEMO_EMAIL)
    await page.get_by_label("Password").fill(DEMO_PASSWORD)
    await user.steps(2)
    await page.get_by_role("button", name="Sign in").click()
    await user.steps(4)


async def download_report(user: ScriptedUser) -> None:
    """The download_report task: sign in, open reports, filter a range, download the CSV."""
    page = user.page
    await sign_in(user)
    await page.get_by_role("link", name="View reports").click()
    await user.steps(5)
    await page.get_by_label("From").fill("2026-02-10")
    await user.steps(6)
    await page.get_by_label("To").fill("2026-04-20")
    await user.steps(7)
    await page.get_by_role("button", name="Apply filter").click()
    await user.steps(8)
    await page.get_by_role("button", name="Download CSV").click()
    await user.steps(9)


class ViewOrderDetail:
    """The view_order_detail task; it also counts what the unscoped View selector matches."""

    def __init__(self, unscoped: Selector) -> None:
        self.unscoped = unscoped
        self.unscoped_matches: int | None = None

    async def __call__(self, user: ScriptedUser) -> None:
        page = user.page
        await sign_in(user)
        await (
            page.get_by_role("navigation", name="Primary")
            .get_by_role("link", name="Orders")
            .click()
        )
        await user.steps(5)
        self.unscoped_matches = (
            await build_locator(page, self.unscoped).filter(visible=True).count()
        )
        await page.get_by_role("row", name="PO-1042").get_by_role("button").click()
        await user.steps(6)
