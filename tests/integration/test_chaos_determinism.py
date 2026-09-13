"""The same URL, seed, and level always produce the same page; levels mean what they say."""

from collections import Counter
from datetime import UTC, datetime

import pytest
from playwright.async_api import Browser

from tests.integration.portal import (
    PAGE_IDS,
    PageId,
    PortalDriver,
    portal_session,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

# Five years ahead of any date in the dataset: a page that read the clock would differ.
FAR_FUTURE = datetime(2031, 6, 15, 9, 30, tzinfo=UTC)


async def _hash_page(driver: PortalDriver, page_id: PageId, seed: int, level: int) -> str:
    if page_id != "login":
        await driver.sign_in()
    await driver.open(page_id, seed=seed, level=level)
    return await driver.dom_hash()


@pytest.mark.parametrize("page_id", PAGE_IDS)
async def test_same_seed_and_level_render_an_identical_dom_across_time_zone_locale_and_clock(
    browser: Browser, portal_url: str, page_id: PageId
) -> None:
    async with portal_session(browser, portal_url, timezone_id="UTC", locale="en-US") as first:
        first_hash = await _hash_page(first, page_id, seed=424242, level=5)

    async with portal_session(
        browser, portal_url, timezone_id="Pacific/Kiritimati", locale="ar-EG"
    ) as second:
        await second.page.clock.install(time=FAR_FUTURE)
        second_hash = await _hash_page(second, page_id, seed=424242, level=5)

    assert first_hash == second_hash


async def test_different_seeds_at_level_3_produce_many_distinct_pages(portal: PortalDriver) -> None:
    await portal.sign_in()
    hashes = []
    for seed in range(1, 21):
        await portal.open("reports", seed=seed, level=3)
        hashes.append(await portal.dom_hash())

    assert len(set(hashes)) >= 15


@pytest.mark.parametrize("page_id", PAGE_IDS)
async def test_level_0_applies_nothing_and_matches_the_unmutated_page(
    portal: PortalDriver, page_id: PageId
) -> None:
    if page_id != "login":
        await portal.sign_in()
    unmutated = await portal.open(page_id)
    unmutated_hash = await portal.dom_hash()

    for seed in (7, 4242):
        state = await portal.open(page_id, seed=seed, level=0)
        assert state.applied == ()
        assert state.error is None
        assert await portal.dom_hash() == unmutated_hash

    assert unmutated.level == 0
    assert unmutated.applied == ()


async def test_chaos_configuration_survives_navigation_without_parameters(
    browser: Browser, portal_url: str
) -> None:
    async with portal_session(browser, portal_url) as navigated:
        await navigated.open("login", seed=31, level=3)
        await (await navigated.locate("login.email")).fill("buyer@harborline.test")
        await (await navigated.locate("login.password")).fill("harbor-demo")
        sign_in = await navigated.locate("login.sign_in")
        async with navigated.expect_page("dashboard"):
            await sign_in.click()
        dashboard = await navigated.state()
        nav_reports = await navigated.locate("dashboard.nav_reports")
        async with navigated.expect_page("reports"):
            await nav_reports.click()
        via_link = await navigated.state()
        via_link_hash = await navigated.dom_hash()
        assert "seed=" not in navigated.page.url

    async with portal_session(browser, portal_url) as direct:
        await direct.sign_in()
        loaded_directly = await direct.open("reports", seed=31, level=3)
        direct_hash = await direct.dom_hash()

    assert (dashboard.seed, dashboard.level, len(dashboard.applied)) == (31, 3, 3)
    assert (via_link.seed, via_link.level) == (31, 3)
    assert via_link.applied == loaded_directly.applied
    assert via_link_hash == direct_hash


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
async def test_each_level_applies_its_exact_mix_of_mutations(
    portal: PortalDriver, level: int
) -> None:
    await portal.sign_in()
    for seed in (1, 2, 3, 4):
        abstain_pages = []
        for page_id in PAGE_IDS:
            state = await portal.open(page_id, seed=seed, level=level)
            categories = Counter(mutation.category for mutation in state.applied)
            is_abstain_page = level >= 4 and state.abstain_page_id == page_id
            if is_abstain_page:
                abstain_pages.append(page_id)

            assert len(state.applied) == level, (page_id, seed, state.applied)
            assert categories["abstain_expected"] == (1 if is_abstain_page else 0)
            assert categories["heal_expected"] == level - (1 if is_abstain_page else 0)
            assert len({mutation.id for mutation in state.applied}) == level

        assert len(abstain_pages) == (1 if level >= 4 else 0)


async def test_every_page_agrees_on_the_abstain_page_and_each_page_gets_a_turn(
    portal: PortalDriver,
) -> None:
    await portal.sign_in()
    chosen: dict[int, PageId] = {}
    for seed in range(1, 17):
        views = {}
        for page_id in PAGE_IDS:
            state = await portal.open(page_id, seed=seed, level=4)
            views[page_id] = state.abstain_page_id
            has_abstain = any(m.category == "abstain_expected" for m in state.applied)
            assert has_abstain == (state.abstain_page_id == page_id)

        assert len(set(views.values())) == 1, (seed, views)
        agreed = views["login"]
        assert agreed is not None
        chosen[seed] = agreed

    assert set(chosen.values()) == set(PAGE_IDS), chosen
