"""The committed heal pair table stays in step with the portal it describes.

If a target declaration, an eligibility rule, or the selection logic changes, these tests
fail, name the pairs that no longer match, and say how to regenerate the table.
"""

from collections.abc import Iterable

import pytest

from benchmarks.chaos.heal_pairs import (
    REGENERATE_HINT,
    TABLE_PATH,
    eligible_pairs,
    load_table,
    render_table,
    selected_targets,
)
from tests.integration.portal import PortalDriver

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]


def _listing(pairs: Iterable[tuple[str, str, str]]) -> str:
    return "".join(
        f"\n  {page}: {mutation} -> {target}" for page, mutation, target in sorted(pairs)
    )


async def test_the_table_file_is_in_canonical_form() -> None:
    table = load_table()

    assert TABLE_PATH.read_text(encoding="utf-8") == render_table(table.pairs), (
        f"heal_pairs.json was edited by hand or is unsorted. {REGENERATE_HINT}"
    )


async def test_the_table_lists_exactly_the_pairs_the_portal_allows(portal: PortalDriver) -> None:
    await portal.sign_in()
    derived = await eligible_pairs(portal.page, portal.base_url)
    recorded = {pair.key for pair in load_table().pairs}

    missing = derived - recorded
    stale = recorded - derived
    assert not missing | stale, (
        "The heal pair table no longer matches the portal's target declarations and "
        f"eligibility rules.\nMissing from the table:{_listing(missing) or ' none'}"
        f"\nIn the table but no longer eligible:{_listing(stale) or ' none'}\n{REGENERATE_HINT}"
    )


async def test_every_seed_still_selects_its_pair(portal: PortalDriver) -> None:
    await portal.sign_in()
    table = load_table()
    selected = await selected_targets(portal.page, portal.base_url, table.pairs)

    wrong = {
        f"{pair.page}: {pair.mutation} seed {pair.seed} now selects {selected[pair]}, "
        f"not {pair.target}"
        for pair in table.pairs
        if selected[pair] != pair.target
    }
    assert not wrong, (
        "The portal's selection logic changed, so recorded seeds pick different targets:\n  "
        + "\n  ".join(sorted(wrong))
        + f"\n{REGENERATE_HINT}"
    )
