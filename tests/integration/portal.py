"""Test-side helpers for driving the chaos portal in a real browser.

Tests may read `window.__chaos`: it is ground truth for tests and benchmarks. Mendwork's
own recorder and healer never may.
"""

import hashlib
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal
from urllib.parse import urlencode

from playwright.async_api import Browser, ConsoleMessage, ElementHandle, Page, ViewportSize
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

PORTAL_ROOT: Final = Path(__file__).resolve().parents[2] / "chaos-portal"
VIEWPORT: Final[ViewportSize] = {"width": 1280, "height": 720}
READY_TIMEOUT_MS: Final = 5_000

DEMO_EMAIL: Final = "buyer@harborline.test"
DEMO_PASSWORD: Final = "harbor-demo"

PageId = Literal["login", "dashboard", "reports", "orders"]
PAGE_IDS: Final[tuple[PageId, ...]] = ("login", "dashboard", "reports", "orders")
PAGE_PATHS: Final[Mapping[PageId, str]] = MappingProxyType(
    {
        "login": "index.html",
        "dashboard": "dashboard.html",
        "reports": "reports.html",
        "orders": "orders.html",
    }
)

_LAYOUT: Final = ("nav_dashboard", "nav_reports", "nav_orders", "sign_out")
TARGET_KEYS: Final[Mapping[PageId, tuple[str, ...]]] = MappingProxyType(
    {
        "login": ("login.email", "login.password", "login.sign_in"),
        "dashboard": (
            *(f"dashboard.{name}" for name in _LAYOUT),
            "dashboard.open_reports",
            "dashboard.open_orders",
        ),
        "reports": (
            *(f"reports.{name}" for name in _LAYOUT),
            "reports.date_from",
            "reports.date_to",
            "reports.apply_filter",
            "reports.download_csv",
        ),
        "orders": (
            *(f"orders.{name}" for name in _LAYOUT),
            "orders.view_order",
            "orders.back_to_orders",
        ),
    }
)
ALL_TARGET_KEYS: Final = frozenset(key for keys in TARGET_KEYS.values() for key in keys)

HEAL_EXPECTED: Final = (
    "button_link_swap",
    "change_ids_classes",
    "synonym_rename",
    "icon_only_aria",
    "reorder_siblings",
    "move_container",
    "extra_wrappers",
    "cookie_banner",
)
ABSTAIN_EXPECTED: Final = ("remove_target", "duplicate_plausible", "dangerous_rename")
ALL_MUTATION_IDS: Final = HEAL_EXPECTED + ABSTAIN_EXPECTED

_STATE_SCRIPT: Final = """() => {
  const c = window.__chaos;
  return {
    seed: c.seed, level: c.level, pageId: c.pageId, ready: c.ready, error: c.error,
    abstainPageId: c.abstainPageId, applied: c.applied, wrongActions: c.wrongActions,
  };
}"""


class FrozenModel(BaseModel):
    """A frozen model that reads the camelCase keys the page returns."""

    model_config = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True)


class AppliedMutation(FrozenModel):
    """One entry of `window.__chaos.applied`."""

    id: str
    category: Literal["heal_expected", "abstain_expected"]
    target_key: str | None
    description: str


class WrongAction(FrozenModel):
    """One entry of `window.__chaos.wrongActions`."""

    mutation_id: Literal["duplicate_plausible", "dangerous_rename"]
    target_key: str
    label: str


class ChaosSnapshot(FrozenModel):
    """`window.__chaos` without `locate`, validated so tests work with typed values."""

    seed: int
    level: int
    page_id: PageId
    ready: bool
    error: str | None
    abstain_page_id: PageId | None
    applied: tuple[AppliedMutation, ...]
    wrong_actions: tuple[WrongAction, ...]


class PortalDriver:
    """Loads portal pages, waits for mutations to finish, and reads ground truth.

    Any uncaught page error or console error fails the next wait, so a broken page can
    never pass a test by accident.
    """

    def __init__(self, page: Page, base_url: str) -> None:
        self.page = page
        self.base_url = base_url
        self._errors: list[str] = []
        page.on("pageerror", lambda error: self._errors.append(f"page error: {error}"))
        page.on("console", self._record_console_error)

    def _record_console_error(self, message: ConsoleMessage) -> None:
        if message.type == "error":
            self._errors.append(f"console error: {message.text}")

    def url(
        self,
        page_id: PageId,
        *,
        seed: int | None = None,
        level: int | None = None,
        only: Sequence[str] = (),
    ) -> str:
        params: dict[str, str] = {}
        if seed is not None:
            params["seed"] = str(seed)
        if level is not None:
            params["level"] = str(level)
        if only:
            params["only"] = ",".join(only)
        query = f"?{urlencode(params, safe=',')}" if params else ""
        return f"{self.base_url}{PAGE_PATHS[page_id]}{query}"

    async def open(
        self,
        page_id: PageId,
        *,
        seed: int | None = None,
        level: int | None = None,
        only: Sequence[str] = (),
    ) -> ChaosSnapshot:
        await self.page.goto(self.url(page_id, seed=seed, level=level, only=only))
        state = await self.wait_ready()
        if state.page_id != page_id:
            raise AssertionError(f"expected the {page_id} page, landed on {state.page_id}")
        return state

    async def wait_ready(self) -> ChaosSnapshot:
        await self.page.wait_for_function(
            "() => window.__chaos?.ready === true", timeout=READY_TIMEOUT_MS
        )
        self.raise_page_errors()
        return await self.state()

    @asynccontextmanager
    async def expect_page(self, page_id: PageId) -> AsyncIterator[None]:
        """Require the action inside the block to load a new document showing `page_id`.

        The current document is marked first, so a navigation back to the same URL (a
        page's own nav link) is still proven to have happened.
        """
        await self.page.evaluate("() => { window.__replacedByNavigation = true; }")
        yield
        await self.page.wait_for_function(
            "() => window.__replacedByNavigation === undefined && window.__chaos?.ready === true",
            timeout=READY_TIMEOUT_MS,
        )
        self.raise_page_errors()
        state = await self.state()
        if state.page_id != page_id:
            raise AssertionError(f"expected the {page_id} page, landed on {state.page_id}")

    def raise_page_errors(self) -> None:
        if self._errors:
            raise AssertionError("the portal reported errors:\n" + "\n".join(self._errors))

    async def state(self) -> ChaosSnapshot:
        return ChaosSnapshot.model_validate(await self.page.evaluate(_STATE_SCRIPT))

    async def dom_hash(self) -> str:
        html = await self.page.evaluate("() => document.documentElement.outerHTML")
        return hashlib.sha256(str(html).encode()).hexdigest()

    async def locate_or_none(self, key: str) -> ElementHandle | None:
        handle = await self.page.evaluate_handle("key => window.__chaos.locate(key)", key)
        element = handle.as_element()
        if element is None:
            await handle.dispose()
        return element

    async def locate(self, key: str) -> ElementHandle:
        element = await self.locate_or_none(key)
        if element is None:
            raise AssertionError(f"target {key} does not exist on this page")
        return element

    async def storage_snapshot(self) -> dict[str, str]:
        """Everything in sessionStorage: the session and the stored chaos configuration."""
        entries = await self.page.evaluate("() => Object.entries(sessionStorage)")
        return {str(key): str(value) for key, value in entries}

    async def is_signed_in(self) -> bool:
        return bool(
            await self.page.evaluate("() => sessionStorage.getItem('harborline.session') !== null")
        )

    async def sign_in(self) -> None:
        """Sign in through the unmutated login form, leaving chaos off until a page sets it."""
        await self.open("login", level=0)
        await self.page.get_by_label("Email address").fill(DEMO_EMAIL)
        await self.page.get_by_label("Password").fill(DEMO_PASSWORD)
        async with self.expect_page("dashboard"):
            await self.page.get_by_role("button", name="Sign in").click()


@asynccontextmanager
async def portal_session(
    browser: Browser,
    base_url: str,
    *,
    timezone_id: str | None = None,
    locale: str | None = None,
) -> AsyncIterator[PortalDriver]:
    """A driver on a fresh browser context, closed afterwards."""
    context = await browser.new_context(
        viewport=VIEWPORT, accept_downloads=True, timezone_id=timezone_id, locale=locale
    )
    # A local static site answers in milliseconds; waiting Playwright's default 30 s only
    # delays a failure.
    context.set_default_timeout(READY_TIMEOUT_MS)
    try:
        yield PortalDriver(await context.new_page(), base_url)
    finally:
        await context.close()


async def is_same_element(page: Page, first: ElementHandle, second: ElementHandle) -> bool:
    return bool(await page.evaluate("([a, b]) => a === b", [first, second]))
