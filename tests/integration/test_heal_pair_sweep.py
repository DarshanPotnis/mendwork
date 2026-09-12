"""Every heal_expected mutation keeps every target it can take visible and working.

One test per pair in benchmarks/chaos/heal_pairs.json. Each loads the page with the pair's
seed and `only=<mutation>`, checks the target's structure, then activates it and checks
the effect a user would see:

- inputs accept and keep what is typed;
- nav links reach their page, and sign out signs out;
- every primary action does its real job;
- swapped controls are activated from the keyboard.

Behaviour is bound to elements through a WeakMap, deliberately invisible to the DOM, so a
binding can only be observed through its effect: activation is the binding check.

The pairs share one browser context and sign in only when a previous pair signed out.
Every load passes its own seed and mutation, so stored chaos configuration from an earlier
pair never applies.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from playwright.async_api import Browser

from benchmarks.chaos.heal_pairs import REGENERATE_HINT, HealPair, load_table
from tests.integration.portal import PortalDriver, portal_session
from tests.integration.probes import Activation, assert_target_works, reveal_target

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

PAIRS = load_table().pairs


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def heal_pair_sweep(browser: Browser, portal_url: str) -> AsyncIterator[PortalDriver]:
    async with portal_session(browser, portal_url) as driver:
        yield driver


async def ensure_signed_in(driver: PortalDriver) -> None:
    on_portal = driver.page.url.startswith(driver.base_url)
    if not (on_portal and await driver.is_signed_in()):
        await driver.sign_in()


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: f"{pair.target}-{pair.mutation}")
async def test_mutation_keeps_the_target_visible_and_working(
    heal_pair_sweep: PortalDriver, pair: HealPair
) -> None:
    if pair.page != "login":
        await ensure_signed_in(heal_pair_sweep)
    state = await heal_pair_sweep.open(pair.page, seed=pair.seed, only=[pair.mutation])

    assert [(m.id, m.target_key) for m in state.applied] == [(pair.mutation, pair.target)], (
        f"seed {pair.seed} no longer applies {pair.mutation} to {pair.target}. {REGENERATE_HINT}"
    )
    await reveal_target(heal_pair_sweep, pair.target)
    element = await heal_pair_sweep.locate(pair.target)
    assert await element.is_visible(), f"{pair.target} is not visible after {pair.mutation}"

    how: Activation = "keyboard" if pair.mutation == "button_link_swap" else "click"
    await assert_target_works(heal_pair_sweep, pair.target, how)
