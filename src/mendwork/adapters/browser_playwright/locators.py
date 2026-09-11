"""Translating domain selectors into Playwright locators: the one place this mapping lives.

Building a locator does not touch the page; Playwright resolves it lazily. Scopes are
applied outermost first, so ``within`` narrows the search before the target is looked
for. Enforcing "exactly one visible element at every level" is the caller's job (the
replayer's Rung 0), and it uses these same locators to count.
"""

from playwright.async_api import Locator, Page

from mendwork.engine.domain.selectors import (
    ByCss,
    ByLabel,
    ByPlaceholder,
    ByRole,
    ByTestId,
    ByText,
    Selector,
    scope_chain,
)


def build_locator(page: Page, selector: Selector) -> Locator:
    """The Playwright locator for a selector, including its scopes."""
    return scope_locators(page, selector)[-1]


def scope_locators(page: Page, selector: Selector) -> tuple[Locator, ...]:
    """A locator for each level of a scoped selector, outermost first, ending with the target.

    Each must resolve to exactly one element for the selector to count as a match.
    """
    chain = scope_chain(selector)
    locators = [_apply(page, chain[0])]
    for level in chain[1:]:
        locators.append(_apply(locators[-1], level))
    return tuple(locators)


def _apply(scope: Page | Locator, selector: Selector) -> Locator:
    match selector:
        case ByTestId():
            return scope.get_by_test_id(selector.value)
        case ByRole():
            return scope.get_by_role(selector.role.value, name=selector.name, exact=selector.exact)
        case ByLabel():
            return scope.get_by_label(selector.value, exact=selector.exact)
        case ByPlaceholder():
            return scope.get_by_placeholder(selector.value, exact=selector.exact)
        case ByText():
            return scope.get_by_text(selector.value, exact=selector.exact)
        case ByCss():
            # The css= prefix stops Playwright guessing XPath or text syntax from the string.
            return scope.locator(f"css={selector.value}")
