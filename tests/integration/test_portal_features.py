"""The portal behaves like a real app: sign-in, CSV export, the Chaos button, and no leaks."""

import asyncio
import csv
import io
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import Browser, Download, Error, Request, expect

from tests.integration.portal import (
    DEMO_EMAIL,
    PAGE_IDS,
    PortalDriver,
    portal_session,
)

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

CSV_HEADER = ["shipment_id", "ship_date", "order_id", "supplier", "sku", "quantity", "amount_usd"]


async def download_csv(portal: PortalDriver) -> tuple[str, list[list[str]]]:
    async with portal.page.expect_download() as download_info:
        await portal.page.get_by_role("button", name="Download CSV").click()
    download = await download_info.value
    path = await download.path()
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    return download.suggested_filename, list(csv.reader(io.StringIO(text)))


async def test_default_range_downloads_the_first_quarter(portal: PortalDriver) -> None:
    await portal.sign_in()
    await portal.open("reports", level=0)

    filename, rows = await download_csv(portal)

    assert filename == "shipments_2026-01-01_to_2026-03-31.csv"
    assert rows[0] == CSV_HEADER
    assert len(rows) - 1 == 18
    assert rows[1] == [
        "SH-26001", "2026-01-05", "PO-1037", "Bluewater Metals", "BW-ALU-2040", "120", "1860.00",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("start", "end", "count", "first", "last"),
    [
        ("2026-02-10", "2026-04-20", 14, "SH-26009", "SH-26022"),
        ("2026-02-14", "2026-02-14", 1, "SH-26009", "SH-26009"),
        ("2026-07-01", "2026-12-31", 0, None, None),
    ],
)
async def test_chosen_range_filters_inclusively(
    portal: PortalDriver, start: str, end: str, count: int, first: str | None, last: str | None
) -> None:
    await portal.sign_in()
    await portal.open("reports", level=0)
    await portal.page.get_by_label("From").fill(start)
    await portal.page.get_by_label("To").fill(end)

    filename, rows = await download_csv(portal)

    assert filename == f"shipments_{start}_to_{end}.csv"
    assert rows[0] == CSV_HEADER
    data = rows[1:]
    assert len(data) == count
    if first is not None:
        assert data[0][0] == first
        assert data[-1][0] == last


async def test_a_reversed_range_is_rejected_without_downloading(portal: PortalDriver) -> None:
    await portal.sign_in()
    await portal.open("reports", level=0)
    downloads: list[Download] = []
    portal.page.on("download", lambda download: downloads.append(download))
    await portal.page.get_by_label("From").fill("2026-05-01")
    await portal.page.get_by_label("To").fill("2026-04-01")

    await portal.page.get_by_role("button", name="Download CSV").click()

    await expect(portal.page.locator("#report-error")).to_have_text(
        "The start date must be on or before the end date."
    )
    await portal.page.evaluate("() => 0")
    assert downloads == []


async def test_pages_request_nothing_but_the_local_portal(
    browser: Browser, portal_url: str
) -> None:
    async with portal_session(browser, portal_url) as portal:
        requested: list[str] = []

        def record(request: Request) -> None:
            requested.append(request.url)

        portal.page.context.on("request", record)
        await portal.sign_in()
        for page_id in PAGE_IDS:
            await portal.open(page_id, seed=2, level=5)
        for page_id in PAGE_IDS:
            await portal.open(page_id, level=0)

    assert requested
    external = [url for url in requested if not url.startswith(portal_url)]
    assert external == []


async def test_signed_out_visitors_are_sent_to_sign_in(portal: PortalDriver) -> None:
    for page_id in ("dashboard", "reports", "orders"):
        async with portal.expect_page("login"):
            # "commit" returns before the page's script redirects, so the redirect is not
            # reported as an interrupted navigation.
            await portal.page.goto(portal.url(page_id), wait_until="commit")


async def test_wrong_credentials_are_refused(portal: PortalDriver) -> None:
    await portal.open("login", level=0)
    await portal.page.get_by_label("Email address").fill(DEMO_EMAIL)
    await portal.page.get_by_label("Password").fill("not-the-password")
    await portal.page.get_by_role("button", name="Sign in").click()

    await expect(portal.page.locator("#login-error")).to_have_text("Incorrect email or password.")
    assert not await portal.is_signed_in()


@pytest.mark.parametrize(
    ("query", "problem"),
    [
        ("level=9", "level must be an integer from 0 to 5"),
        ("seed=-4&level=2", "seed must be an integer"),
        ("seed=4294967296&level=2", "seed must be an integer"),
        ("only=teleport", "unknown: teleport"),
        ("only=cookie_banner,cookie_banner", "must not repeat"),
        ("level=0&only=cookie_banner", "only needs a level from 1 to 5"),
    ],
)
async def test_invalid_parameters_apply_nothing_and_keep_the_stored_configuration(
    portal: PortalDriver, query: str, problem: str
) -> None:
    await portal.open("login", seed=5, level=2)

    await portal.page.goto(f"{portal.base_url}index.html?{query}")
    rejected = await portal.wait_ready()
    await expect(portal.page.get_by_role("alert").filter(has_text=problem)).to_be_visible()
    await portal.page.goto(f"{portal.base_url}index.html")
    restored = await portal.wait_ready()

    assert rejected.error is not None
    assert problem in rejected.error
    assert rejected.applied == ()
    assert rejected.level == 0
    assert (restored.seed, restored.level, restored.error) == (5, 2, None)


async def test_locating_an_unknown_target_is_an_error(portal: PortalDriver) -> None:
    await portal.open("login", level=0)

    with pytest.raises(Error, match=r"unknown target key reports\.download_csv"):
        await portal.locate_or_none("reports.download_csv")


@pytest.mark.parametrize(
    ("start_level", "only", "expected_level"),
    [(3, (), "3"), (0, (), "3"), (2, ("cookie_banner",), "2")],
)
async def test_chaos_button_reloads_with_a_reproducible_new_seed(
    portal: PortalDriver, start_level: int, only: tuple[str, ...], expected_level: str
) -> None:
    await portal.sign_in()
    await portal.open("reports", seed=5, level=start_level, only=only)

    async with portal.expect_page("reports"):
        await portal.page.get_by_role("button", name="Chaos").click()
    scrambled = await portal.state()

    params = parse_qs(urlsplit(portal.page.url).query)
    assert params["level"] == [expected_level]
    assert params.get("only", []) == ([",".join(only)] if only else [])
    assert scrambled.seed == int(params["seed"][0])
    assert scrambled.level == int(expected_level)
    assert scrambled.error is None
