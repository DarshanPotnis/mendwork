"""Rung 0's primitive: count visible matches at every scope level and pin a unique element.

Each level is searched inside the single visible element the level above found, and only
visible elements count, so a hidden duplicate cannot make a visible control ambiguous and
a hidden control can never be the target.
"""

from dataclasses import dataclass

from playwright.async_api import ElementHandle, Locator, Page

from mendwork.adapters.browser_playwright.locators import apply_selector
from mendwork.engine.domain.selectors import Selector, scope_chain


@dataclass(frozen=True, slots=True)
class UniqueResolution:
    """Visible matches per level, and the element when every level matched exactly one."""

    level_counts: tuple[int, ...]
    element: ElementHandle | None


async def resolve_unique(page: Page, selector: Selector) -> UniqueResolution:
    """Resolve a selector level by level, stopping at the first level that is not unique.

    The final level is read as element handles rather than counted, so the element pinned
    is exactly one of the elements that were counted.
    """
    chain = scope_chain(selector)
    counts: list[int] = []
    scope: Page | Locator = page
    for depth, level in enumerate(chain):
        locator = apply_selector(scope, level).filter(visible=True)
        if depth < len(chain) - 1:
            count = await locator.count()
            counts.append(count)
            if count != 1:
                return UniqueResolution(tuple(counts), None)
            scope = locator
            continue
        handles = await locator.element_handles()
        counts.append(len(handles))
        if len(handles) == 1:
            return UniqueResolution(tuple(counts), handles[0])
        for handle in handles:
            await handle.dispose()
    return UniqueResolution(tuple(counts), None)
