"""Each mutation, applied alone with only=, has its specific, verifiable effect."""

import re
from typing import Literal

import pytest
from playwright.async_api import ElementHandle, Locator, expect

from tests.integration.portal import (
    ALL_TARGET_KEYS,
    PAGE_IDS,
    READY_TIMEOUT_MS,
    TARGET_KEYS,
    AppliedMutation,
    FrozenModel,
    PageId,
    PortalDriver,
    is_same_element,
)
from tests.integration.probes import PROBES, assert_target_works

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

_DESCRIBE = """element => {
  const squash = (text) => (text ?? "").replace(/\\s+/g, " ").trim();
  const signature = (node) => `${node.tagName}#${node.id}.${node.getAttribute("class") ?? ""}`;
  const ancestors = [];
  const path = [];
  for (let node = element; node.parentElement !== null; node = node.parentElement) {
    path.push(Array.prototype.indexOf.call(node.parentElement.children, node));
    ancestors.push(signature(node.parentElement));
  }
  const visible = element.cloneNode(true);
  for (const hidden of visible.querySelectorAll(".visually-hidden")) hidden.remove();
  const labels = element.labels ?? null;
  return {
    tag: element.tagName,
    elementId: element.id,
    className: element.getAttribute("class"),
    testId: element.getAttribute("data-testid"),
    ariaLabel: element.getAttribute("aria-label"),
    name: squash(element.getAttribute("aria-label") ?? element.textContent),
    visibleText: squash(visible.textContent),
    labelText: labels && labels.length > 0 ? squash(labels[0].textContent) : null,
    labelCount: labels ? labels.length : null,
    hasIcon: element.querySelector("svg") !== null,
    ancestors,
    path,
  };
}"""

_UNCOVERED_AT_CENTRE = """element => {
  const box = element.getBoundingClientRect();
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return hit !== null && (hit === element || element.contains(hit));
}"""


class Description(FrozenModel):
    """What a test can observe about one element."""

    tag: str
    element_id: str
    class_name: str | None
    test_id: str | None
    aria_label: str | None
    name: str
    visible_text: str
    label_text: str | None
    label_count: int | None
    has_icon: bool
    ancestors: tuple[str, ...]
    path: tuple[int, ...]


async def describe(element: ElementHandle) -> Description:
    return Description.model_validate(await element.evaluate(_DESCRIBE))


def role_for(tag: str) -> Literal["link", "button"]:
    if tag == "A":
        return "link"
    if tag == "BUTTON":
        return "button"
    raise AssertionError(f"no ARIA role mapping for <{tag.lower()}>")


def by_role(portal: PortalDriver, tag: str, name: str) -> Locator:
    """Controls with this role and exact accessible name, as the browser computes it."""
    return portal.page.get_by_role(role_for(tag), name=name, exact=True, include_hidden=True)


async def apply_alone(
    portal: PortalDriver, page_id: PageId, mutation_id: str, seed: int = 1
) -> AppliedMutation:
    if page_id != "login":
        await portal.sign_in()
    state = await portal.open(page_id, seed=seed, only=[mutation_id])
    assert len(state.applied) == 1, state.applied
    assert state.applied[0].id == mutation_id
    return state.applied[0]


async def apply_to_target(
    portal: PortalDriver, page_id: PageId, mutation_id: str, seed: int = 1
) -> tuple[str, Description]:
    """Apply one mutation, capture its target on the unmutated page, then reload mutated."""
    applied = await apply_alone(portal, page_id, mutation_id, seed)
    if applied.target_key is None:
        raise AssertionError(f"{mutation_id} reported no target")
    await portal.open(page_id, level=0)
    before = await describe(await portal.locate(applied.target_key))
    await portal.open(page_id, seed=seed, only=[mutation_id])
    return applied.target_key, before


async def test_every_target_has_a_probe() -> None:
    assert set(PROBES) == ALL_TARGET_KEYS


async def test_every_declared_target_exists_on_the_unmutated_pages(portal: PortalDriver) -> None:
    await portal.sign_in()
    for page_id, keys in TARGET_KEYS.items():
        await portal.open(page_id, level=0)
        for key in keys:
            assert await portal.locate_or_none(key) is not None, key


async def test_synonym_rename_changes_the_wording_and_the_control_still_works(
    portal: PortalDriver,
) -> None:
    key, before = await apply_to_target(portal, "reports", "synonym_rename")
    element = await portal.locate(key)
    after = await describe(element)

    assert after.tag == before.tag
    if before.label_text is None:
        assert after.name != before.name
        renamed = await by_role(portal, after.tag, after.name).element_handle(
            timeout=READY_TIMEOUT_MS
        )
    else:
        assert after.label_text not in (None, before.label_text)
        renamed = await portal.page.get_by_label(str(after.label_text), exact=True).element_handle(
            timeout=READY_TIMEOUT_MS
        )
    assert await is_same_element(portal.page, renamed, element)
    await assert_target_works(portal, key)


async def test_reorder_siblings_moves_the_target_within_the_same_structure(
    portal: PortalDriver,
) -> None:
    key, before = await apply_to_target(portal, "orders", "reorder_siblings")
    after = await describe(await portal.locate(key))

    assert after.path != before.path
    assert after.ancestors == before.ancestors
    await assert_target_works(portal, key)


async def test_change_ids_classes_regenerates_every_identifying_attribute(
    portal: PortalDriver,
) -> None:
    key, before = await apply_to_target(portal, "login", "change_ids_classes")
    after = await describe(await portal.locate(key))

    if before.element_id:
        assert after.element_id not in ("", before.element_id)
    if before.class_name is not None:
        old_classes = before.class_name.split()
        new_classes = (after.class_name or "").split()
        assert len(new_classes) == len(old_classes)
        for old, new in zip(old_classes, new_classes, strict=True):
            assert new.startswith(f"{old}-")
    if before.test_id is not None:
        assert after.test_id not in (None, before.test_id)
    if before.label_count is not None:
        assert after.label_count == 1
        assert after.label_text == before.label_text
    await assert_target_works(portal, key)


async def test_extra_wrappers_add_containers_above_the_original_ancestry(
    portal: PortalDriver,
) -> None:
    key, before = await apply_to_target(portal, "dashboard", "extra_wrappers")
    after = await describe(await portal.locate(key))

    added = len(after.ancestors) - len(before.ancestors)
    assert 1 <= added <= 3
    assert after.ancestors[added:] == before.ancestors
    await assert_target_works(portal, key)


async def test_move_container_places_the_control_in_a_different_section(
    portal: PortalDriver,
) -> None:
    key, before = await apply_to_target(portal, "reports", "move_container")
    after = await describe(await portal.locate(key))

    assert before.ancestors[0] not in after.ancestors
    assert after.name == before.name
    await assert_target_works(portal, key)


async def test_button_link_swap_keeps_the_name_and_works_from_the_keyboard(
    portal: PortalDriver,
) -> None:
    key, before = await apply_to_target(portal, "orders", "button_link_swap")
    element = await portal.locate(key)
    after = await describe(element)

    assert {before.tag, after.tag} == {"A", "BUTTON"}
    assert after.name == before.name
    swapped = await by_role(portal, after.tag, after.name).element_handle(timeout=READY_TIMEOUT_MS)
    assert await is_same_element(portal.page, swapped, element)
    await assert_target_works(portal, key, how="keyboard")


async def test_icon_only_aria_hides_the_text_but_keeps_the_accessible_name(
    portal: PortalDriver,
) -> None:
    key, before = await apply_to_target(portal, "reports", "icon_only_aria")
    element = await portal.locate(key)
    after = await describe(element)

    assert after.has_icon
    assert after.visible_text == ""
    assert after.aria_label == before.name
    named = await by_role(portal, after.tag, before.name).element_handle(timeout=READY_TIMEOUT_MS)
    assert await is_same_element(portal.page, named, element)
    await assert_target_works(portal, key)


PRIMARY_ACTION = {
    "login": "login.sign_in",
    "dashboard": "dashboard.open_reports",
    "reports": "reports.download_csv",
    "orders": "orders.view_order",
}


@pytest.mark.parametrize("page_id", PAGE_IDS)
async def test_cookie_banner_covers_no_target_and_can_be_dismissed(
    portal: PortalDriver, page_id: PageId
) -> None:
    applied = await apply_alone(portal, page_id, "cookie_banner")
    assert applied.target_key is None

    banner = portal.page.locator("body > section.cookie-consent")
    await expect(banner).to_be_visible()
    await expect(banner.get_by_role("button")).to_have_count(2)

    for key in TARGET_KEYS[page_id]:
        element = await portal.locate(key)
        if not await element.is_visible():
            continue
        await element.scroll_into_view_if_needed()
        box = await element.bounding_box()
        banner_box = await banner.bounding_box()
        if box is None or banner_box is None:
            raise AssertionError(f"{key} or the banner has no layout box")
        assert box["y"] >= banner_box["y"] + banner_box["height"], key
        assert await element.evaluate(_UNCOVERED_AT_CENTRE), f"{key} is covered at its centre"

    await banner.get_by_role("button").first.click()
    await expect(banner).to_have_count(0)
    await assert_target_works(portal, PRIMARY_ACTION[page_id])


async def test_remove_target_leaves_no_control_with_that_name(portal: PortalDriver) -> None:
    key, before = await apply_to_target(portal, "reports", "remove_target")

    assert await portal.locate_or_none(key) is None
    await expect(by_role(portal, before.tag, before.name)).to_have_count(0)


async def test_duplicate_plausible_leaves_two_indistinguishable_adjacent_copies(
    portal: PortalDriver,
) -> None:
    key, before = await apply_to_target(portal, "dashboard", "duplicate_plausible")
    located = await portal.locate(key)

    copies = by_role(portal, before.tag, before.name)
    await expect(copies).to_have_count(2)
    first, second = await copies.element_handles()
    assert await first.evaluate("(a, b) => a.nextElementSibling === b", second)
    matches = [await is_same_element(portal.page, copy, located) for copy in (first, second)]
    assert matches.count(True) == 1
    for copy in (first, second):
        described = await describe(copy)
        assert described.element_id not in ("", before.element_id)
        assert described.test_id is None


async def test_dangerous_rename_gives_the_target_a_dangerous_label(portal: PortalDriver) -> None:
    key, before = await apply_to_target(portal, "orders", "dangerous_rename")
    after = await describe(await portal.locate(key))

    assert after.tag == before.tag
    assert after.name != before.name
    assert re.match(r"^(Delete|Cancel|Purge)\b", after.name), after.name
