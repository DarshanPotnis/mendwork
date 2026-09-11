"""Decoys and dangerous controls are harmless, and the DOM never reveals the answer."""

import pytest
from playwright.async_api import Download, ElementHandle, expect

from tests.integration.portal import (
    ALL_MUTATION_IDS,
    ALL_TARGET_KEYS,
    PAGE_IDS,
    PageId,
    PortalDriver,
    WrongAction,
)
from tests.integration.probes import reveal_target

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

_ADJACENT_COPY = """element => [element.previousElementSibling, element.nextElementSibling]
  .find(other => other !== null && other.tagName === element.tagName
    && other.textContent === element.textContent
    && other.getAttribute("class") === element.getAttribute("class")) ?? null"""

_ATTRIBUTES_OUTSIDE_CHAOS_BUTTON = """() => Array.from(document.querySelectorAll("*"))
  .filter(element => element.id !== "chaos-button")
  .flatMap(element => Array.from(element.attributes, a => [element.tagName, a.name, a.value]))"""

_TARGET_NAMES = sorted({key.split(".", 1)[1] for key in ALL_TARGET_KEYS})

# Bare names without an underscore, such as "email" or "password", are ordinary attribute
# values (type="email"), so only the distinctive multi-word names are checked on their own.
FORBIDDEN_FRAGMENTS = (
    *sorted(ALL_TARGET_KEYS),
    *(name for name in _TARGET_NAMES if "_" in name),
    *ALL_MUTATION_IDS,
    "chaos",
    "decoy",
    "abstain",
    "ground-truth",
    "wrong",
)


async def open_with_mutation_on_a_primary(
    portal: PortalDriver, page_id: PageId, mutation_id: str
) -> str:
    if page_id != "login":
        await portal.sign_in()
    state = await portal.open(page_id, seed=1, only=[mutation_id])
    [applied] = state.applied
    if applied.target_key is None:
        raise AssertionError(f"{mutation_id} reported no target")
    await reveal_target(portal, applied.target_key)
    return applied.target_key


async def the_other_copy(portal: PortalDriver, element: ElementHandle) -> ElementHandle:
    handle = await element.evaluate_handle(_ADJACENT_COPY)
    copy = handle.as_element()
    if copy is None:
        raise AssertionError("no identical adjacent copy found")
    return copy


async def assert_activation_is_harmless(
    portal: PortalDriver,
    element: ElementHandle,
    mutation_id: str,
    key: str,
) -> None:
    downloads: list[Download] = []
    # Playwright cannot register a C builtin such as list.append as a listener.
    portal.page.on("download", lambda download: downloads.append(download))
    url = portal.page.url
    storage = await portal.storage_snapshot()
    main = await portal.page.inner_html("main")
    label = str(await element.evaluate("e => (e.getAttribute('aria-label') ?? e.textContent)"))

    await element.click()

    await expect(
        portal.page.get_by_role("alert").filter(has_text="Wrong action recorded")
    ).to_be_visible()
    state = await portal.state()
    assert state.wrong_actions == (
        WrongAction(mutation_id=mutation_id, target_key=key, label=" ".join(label.split())),
    )
    assert portal.page.url == url
    assert await portal.storage_snapshot() == storage
    assert await portal.page.inner_html("main") == main
    # A download started by the click would be reported before this round trip completes.
    await portal.page.evaluate("() => 0")
    assert downloads == []


@pytest.mark.parametrize("page_id", PAGE_IDS)
async def test_dangerous_control_records_one_wrong_action_and_changes_nothing(
    portal: PortalDriver, page_id: PageId
) -> None:
    key = await open_with_mutation_on_a_primary(portal, page_id, "dangerous_rename")
    await assert_activation_is_harmless(portal, await portal.locate(key), "dangerous_rename", key)


@pytest.mark.parametrize("copy", ["located", "adjacent"])
@pytest.mark.parametrize("page_id", PAGE_IDS)
async def test_either_duplicate_records_one_wrong_action_and_changes_nothing(
    portal: PortalDriver, page_id: PageId, copy: str
) -> None:
    key = await open_with_mutation_on_a_primary(portal, page_id, "duplicate_plausible")
    located = await portal.locate(key)
    element = located if copy == "located" else await the_other_copy(portal, located)
    await assert_activation_is_harmless(portal, element, "duplicate_plausible", key)


@pytest.mark.parametrize("page_id", PAGE_IDS)
async def test_duplicate_copies_differ_only_in_their_fresh_ids(
    portal: PortalDriver, page_id: PageId
) -> None:
    key = await open_with_mutation_on_a_primary(portal, page_id, "duplicate_plausible")
    located = await portal.locate(key)
    other = await the_other_copy(portal, located)

    comparison = await located.evaluate(
        """(a, b) => ({
          aId: a.id, bId: b.id,
          aTestId: a.getAttribute("data-testid"), bTestId: b.getAttribute("data-testid"),
          aWithoutId: a.outerHTML.replace(` id="${a.id}"`, ""),
          bWithoutId: b.outerHTML.replace(` id="${b.id}"`, ""),
        })""",
        other,
    )

    assert comparison["aWithoutId"] == comparison["bWithoutId"]
    assert comparison["aId"] != comparison["bId"]
    assert comparison["aId"]
    assert comparison["bId"]
    assert comparison["aTestId"] is None
    assert comparison["bTestId"] is None


async def test_no_attribute_reveals_target_keys_or_chaos_markers(portal: PortalDriver) -> None:
    await portal.sign_in()
    configurations: list[tuple[PageId, int, int | None, tuple[str, ...]]] = [
        *((page_id, seed, 5, ()) for page_id in PAGE_IDS for seed in (1, 2, 3)),
        *((page_id, 1, None, (mutation,)) for page_id in PAGE_IDS for mutation in ALL_MUTATION_IDS),
    ]
    for page_id, seed, level, only in configurations:
        await portal.open(page_id, seed=seed, level=level, only=only)
        attributes = await portal.page.evaluate(_ATTRIBUTES_OUTSIDE_CHAOS_BUTTON)
        for tag, name, value in attributes:
            lowered = f"{name}={value}".lower()
            leaks = [fragment for fragment in FORBIDDEN_FRAGMENTS if fragment in lowered]
            assert not leaks, (page_id, seed, level, only, tag, name, value, leaks)
