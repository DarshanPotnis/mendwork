"""The committed pair tables (heal and abstain) stay in step with the portal they describe.

If a target declaration, an eligibility rule, or the selection logic changes, these tests
fail, name the pairs that no longer match, and say how to regenerate the table.
"""

from collections.abc import Iterable

import pytest

from benchmarks.chaos.heal_pairs import (
    ABSTAIN,
    HEAL,
    REGENERATE_HINT,
    TABLE_PATHS,
    Category,
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


CATEGORIES = pytest.mark.parametrize("category", [HEAL, ABSTAIN])


@CATEGORIES
async def test_the_table_file_is_in_canonical_form(category: Category) -> None:
    path = TABLE_PATHS[category]
    table = load_table(path)

    assert path.read_text(encoding="utf-8") == render_table(table.pairs, category), (
        f"{path.name} was edited by hand or is unsorted. {REGENERATE_HINT}"
    )


@CATEGORIES
async def test_the_table_lists_exactly_the_pairs_the_portal_allows(
    portal: PortalDriver, category: Category
) -> None:
    await portal.sign_in()
    derived = await eligible_pairs(portal.page, portal.base_url, category)
    recorded = {pair.key for pair in load_table(TABLE_PATHS[category]).pairs}

    missing = derived - recorded
    stale = recorded - derived
    assert not missing | stale, (
        f"The {category} pair table no longer matches the portal's target declarations and "
        f"eligibility rules.\nMissing from the table:{_listing(missing) or ' none'}"
        f"\nIn the table but no longer eligible:{_listing(stale) or ' none'}\n{REGENERATE_HINT}"
    )


@CATEGORIES
async def test_every_seed_still_selects_its_pair(portal: PortalDriver, category: Category) -> None:
    await portal.sign_in()
    table = load_table(TABLE_PATHS[category])
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
