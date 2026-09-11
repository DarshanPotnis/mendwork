"""Proof that a target still does its job after a heal_expected mutation.

Each probe activates one logical target and checks the effect a user would see, so a
mutation that silently broke a control fails its test.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Final, Literal

from playwright.async_api import ElementHandle, expect

from tests.integration.portal import DEMO_EMAIL, DEMO_PASSWORD, PageId, PortalDriver

Activation = Literal["click", "keyboard"]
Probe = Callable[[PortalDriver, Activation], Awaitable[None]]


async def activate(driver: PortalDriver, element: ElementHandle, how: Activation) -> None:
    if how == "click":
        await element.click()
    else:
        await element.focus()
        await driver.page.keyboard.press("Enter")


async def type_and_keep(element: ElementHandle, value: str) -> None:
    """Type into a field and prove the field kept exactly what was typed."""
    await element.fill(value)
    assert await element.input_value() == value


def _navigates(key: str, destination: PageId) -> Probe:
    async def probe(driver: PortalDriver, how: Activation) -> None:
        element = await driver.locate(key)
        async with driver.expect_page(destination):
            await activate(driver, element, how)

    return probe


def _signs_out(key: str) -> Probe:
    async def probe(driver: PortalDriver, how: Activation) -> None:
        element = await driver.locate(key)
        async with driver.expect_page("login"):
            await activate(driver, element, how)
        assert not await driver.is_signed_in()

    return probe


async def _signs_in(driver: PortalDriver, how: Activation) -> None:
    await type_and_keep(await driver.locate("login.email"), DEMO_EMAIL)
    await type_and_keep(await driver.locate("login.password"), DEMO_PASSWORD)
    sign_in = await driver.locate("login.sign_in")
    async with driver.expect_page("dashboard"):
        await activate(driver, sign_in, how)
    assert await driver.is_signed_in()


async def _filters_reports(driver: PortalDriver, how: Activation) -> None:
    await type_and_keep(await driver.locate("reports.date_from"), "2026-02-10")
    await type_and_keep(await driver.locate("reports.date_to"), "2026-04-20")
    await activate(driver, await driver.locate("reports.apply_filter"), how)
    await expect(driver.page.locator("#report-summary")).to_have_text(
        "14 shipments between Feb 10, 2026 and Apr 20, 2026."
    )


async def _downloads_csv(driver: PortalDriver, how: Activation) -> None:
    async with driver.page.expect_download() as download_info:
        await activate(driver, await driver.locate("reports.download_csv"), how)
    download = await download_info.value
    assert download.suggested_filename == "shipments_2026-01-01_to_2026-03-31.csv"
    text = await asyncio.to_thread((await download.path()).read_text, encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "shipment_id,ship_date,order_id,supplier,sku,quantity,amount_usd"
    assert len(lines) - 1 == 18


async def _views_order(driver: PortalDriver, how: Activation) -> None:
    await activate(driver, await driver.locate("orders.view_order"), how)
    await expect(driver.page.locator("#order-detail")).to_be_visible()
    await expect(driver.page.locator("#detail-order-id")).to_have_text("PO-1042")


async def _returns_to_orders(driver: PortalDriver, how: Activation) -> None:
    detail = driver.page.locator("#order-detail")
    if await detail.is_hidden():
        await (await driver.locate("orders.view_order")).click()
    await expect(detail).to_be_visible()
    await activate(driver, await driver.locate("orders.back_to_orders"), how)
    await expect(driver.page.locator("#orders-list")).to_be_visible()
    await expect(driver.page.locator("#order-detail")).to_be_hidden()


def _layout_probes(page_id: PageId) -> dict[str, Probe]:
    return {
        f"{page_id}.nav_dashboard": _navigates(f"{page_id}.nav_dashboard", "dashboard"),
        f"{page_id}.nav_reports": _navigates(f"{page_id}.nav_reports", "reports"),
        f"{page_id}.nav_orders": _navigates(f"{page_id}.nav_orders", "orders"),
        f"{page_id}.sign_out": _signs_out(f"{page_id}.sign_out"),
    }


PROBES: Final[Mapping[str, Probe]] = MappingProxyType(
    {
        "login.email": _signs_in,
        "login.password": _signs_in,
        "login.sign_in": _signs_in,
        **_layout_probes("dashboard"),
        "dashboard.open_reports": _navigates("dashboard.open_reports", "reports"),
        "dashboard.open_orders": _navigates("dashboard.open_orders", "orders"),
        **_layout_probes("reports"),
        "reports.date_from": _filters_reports,
        "reports.date_to": _filters_reports,
        "reports.apply_filter": _filters_reports,
        "reports.download_csv": _downloads_csv,
        **_layout_probes("orders"),
        "orders.view_order": _views_order,
        "orders.back_to_orders": _returns_to_orders,
    }
)


async def assert_target_works(driver: PortalDriver, key: str, how: Activation = "click") -> None:
    await PROBES[key](driver, how)


async def reveal_target(driver: PortalDriver, key: str) -> None:
    """Bring a target that starts hidden into view without touching the target itself.

    Only the order detail's controls start hidden; opening a different order than
    PO-1042 shows the panel while leaving `orders.view_order` unused.
    """
    if key == "orders.back_to_orders":
        await driver.page.locator("#view-po-1037").click()
        await expect(driver.page.locator("#order-detail")).to_be_visible()
