"""Selectors: ranked, explicit ways to find a step's target, optionally scoped.

A selector may declare a ``within`` selector because real pages repeat identical controls
(every order row has a "View" button). The target is searched only inside the element
the scope resolves to, and every level must resolve to exactly one element at run time.
Scopes nest at most ``SELECTOR_SCOPE_MAX_DEPTH`` levels: deeper chains encode layout the
fingerprint's structural path already records, and each level is one more thing to break.
"""

import re
from typing import TYPE_CHECKING, Annotated, Final, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from mendwork.engine.domain.base import DomainModel, LongText, Text
from mendwork.engine.domain.enums import AriaRole, SelectorStrategy
from mendwork.engine.domain.limits import SELECTOR_SCOPE_MAX_DEPTH

# Playwright's selector language extends CSS. A css selector here must be plain CSS, so the
# same string also works with querySelectorAll in page scripts, and scoping cannot be
# smuggled in through ">>" chains that bypass the depth limit and exactly-one rule.
_NON_STANDARD_CSS: Final = re.compile(
    r">>"
    r"|^\s*[A-Za-z_-]+\s*="
    r"|^\s*(?://|\.\.)"
    r"|:(?:has-text|text-is|text-matches|text|visible|nth-match|left-of|right-of|above|below|near)\b"
    r"|internal:"
)


def check_plain_css(value: str) -> str:
    """Reject Playwright-only selector syntax in a css selector."""
    if _NON_STANDARD_CSS.search(value):
        raise ValueError(
            "must be plain CSS: Playwright extensions such as '>>', 'text=', ':has-text()', "
            "and ':visible' are not allowed; use 'within' to scope a selector"
        )
    return value


class _ScopedSelector(DomainModel):
    if TYPE_CHECKING:
        # Each concrete selector declares the field itself, last, so files read naturally.
        within: "Selector | None"

    @model_validator(mode="after")
    def _limit_scope_depth(self) -> Self:
        depth = 0
        scope = self.within
        while scope is not None:
            depth += 1
            scope = scope.within
        if depth > SELECTOR_SCOPE_MAX_DEPTH:
            raise ValueError(
                f"'within' nests {depth} levels deep; "
                f"at most {SELECTOR_SCOPE_MAX_DEPTH} are allowed"
            )
        return self


class ByTestId(_ScopedSelector):
    """The element whose ``data-testid`` attribute equals ``value``."""

    strategy: Literal[SelectorStrategy.TEST_ID]
    value: Text
    within: "Selector | None" = None
    """Search only inside the single element this selector finds."""


class ByRole(_ScopedSelector):
    """The element with this ARIA role and accessible name."""

    strategy: Literal[SelectorStrategy.ROLE_NAME]
    role: AriaRole
    name: Text
    exact: bool = True
    """True: the whole name, case-sensitive. False: a case-insensitive substring."""
    within: "Selector | None" = None
    """Search only inside the single element this selector finds."""


class ByLabel(_ScopedSelector):
    """The form control labelled ``value`` (label element, aria-labelledby, or aria-label)."""

    strategy: Literal[SelectorStrategy.LABEL]
    value: Text
    exact: bool = True
    """True: the whole label, case-sensitive. False: a case-insensitive substring."""
    within: "Selector | None" = None
    """Search only inside the single element this selector finds."""


class ByPlaceholder(_ScopedSelector):
    """The input whose placeholder is ``value``."""

    strategy: Literal[SelectorStrategy.PLACEHOLDER]
    value: Text
    exact: bool = True
    """True: the whole placeholder, case-sensitive. False: a case-insensitive substring."""
    within: "Selector | None" = None
    """Search only inside the single element this selector finds."""


class ByText(_ScopedSelector):
    """The smallest element whose visible text is ``value``."""

    strategy: Literal[SelectorStrategy.TEXT]
    value: Text
    exact: bool = True
    """True: the whole text, case-sensitive. False: a case-insensitive substring."""
    within: "Selector | None" = None
    """Search only inside the single element this selector finds."""


class ByCss(_ScopedSelector):
    """The element matching a plain CSS selector."""

    strategy: Literal[SelectorStrategy.CSS]
    value: Annotated[LongText, AfterValidator(check_plain_css)]
    within: "Selector | None" = None
    """Search only inside the single element this selector finds."""


Selector = Annotated[
    ByTestId | ByRole | ByLabel | ByPlaceholder | ByText | ByCss,
    Field(discriminator="strategy"),
]

for _model in (ByTestId, ByRole, ByLabel, ByPlaceholder, ByText, ByCss):
    _model.model_rebuild()


def scope_chain(selector: Selector) -> tuple[Selector, ...]:
    """The selector and its scopes, outermost scope first: the order they resolve in."""
    chain: list[Selector] = []
    current: Selector | None = selector
    while current is not None:
        chain.append(current)
        current = current.within
    return tuple(reversed(chain))
