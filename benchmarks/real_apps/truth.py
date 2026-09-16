"""Ground truth on a real application: a person's labels, resolved on the page (ADR 0014).

The chaos portal answers "is this element the step's control?" itself. A real application cannot,
so the benchmark resolves the person's label for the step and compares the element it finds with
the element the system is about to act on. A label that no longer matches exactly one element on
the page matches nothing: an ambiguous label must never count as ground truth.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import JSHandle, Page

SAME_ELEMENT: Final = "([left, right]) => left === right"
TARGET_INDEX: Final = "([target, elements]) => elements.findIndex((item) => item === target)"
RESOLVE_TIMEOUT_MS: Final = 1_000


@dataclass(frozen=True, slots=True)
class LabelProbes:
    """The ground-truth probes for one release, from the labels a person approved."""

    selectors: Mapping[str, str]

    async def matched(self, page: Page, keys: Sequence[str], element: JSHandle) -> tuple[str, ...]:
        """Every labelled control the element really is, on the page as it is now."""
        found: list[str] = []
        for key in keys:
            labelled = await self._element(page, key)
            if labelled is None:
                continue
            try:
                same = await page.evaluate(SAME_ELEMENT, [labelled, element])
            except PlaywrightError:
                continue
            if same:
                found.append(key)
        return tuple(found)

    async def target_index(self, page: Page, key: str, handles: Sequence[JSHandle]) -> int:
        """Which of a scan's candidates is the labelled control; -1 when none of them is."""
        labelled = await self._element(page, key)
        if labelled is None:
            return -1
        try:
            index = await page.evaluate(TARGET_INDEX, [labelled, list(handles)])
        except PlaywrightError:
            return -1
        return index if isinstance(index, int) else -1

    async def available(self, page: Page, key: str) -> bool:
        """Whether the labelled control is on the page and visible, so acting was possible."""
        return await self._element(page, key) is not None

    async def known(self, page: Page, key: str) -> bool:
        """Whether the label names exactly one visible element now, so an action can be judged.

        This is the same question as ``available``, asked for a different purpose: when it is
        false at the moment of an action, the benchmark did not see what the action reached and
        must record the action as unjudged rather than as a wrong one.
        """
        return await self.available(page, key)

    async def wrong_count(self, page: Page) -> int | None:
        """A real application records no wrong actions of its own; only ground truth can tell."""
        return None

    async def _element(self, page: Page, key: str) -> JSHandle | None:
        selector = self.selectors.get(key)
        if selector is None:
            return None
        try:
            locator = page.locator(selector)
            if await locator.count() != 1 or not await locator.is_visible():
                return None
            return await locator.element_handle(timeout=RESOLVE_TIMEOUT_MS)
        except PlaywrightError:
            return None
