"""Fingerprints: everything recorded about a target, so one changed clue doesn't lose it."""

import math
import re
from typing import Annotated, Final, Self

from pydantic import AfterValidator, Field, field_validator, model_validator

from mendwork.engine.domain.base import DomainModel, LongText, ShortText, Text
from mendwork.engine.domain.enums import AriaRole
from mendwork.engine.domain.limits import NEARBY_TEXT_MAX_ITEMS, SELECTORS_MAX_ITEMS
from mendwork.engine.domain.selectors import Selector

_TAG: Final = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
# Measured boxes that touch an edge can sum to 1.0000000000000002 in floating point.
_EDGE_TOLERANCE: Final = 1e-9


def check_tag(value: str) -> str:
    """Accept a lowercase HTML or custom-element tag name."""
    if _TAG.fullmatch(value) is None:
        raise ValueError("must be a lowercase HTML tag name, such as 'button' or 'my-widget'")
    return value


def check_href_path(value: str) -> str:
    """Accept only the path of a link, never its host, query, or fragment.

    Query strings and fragments carry session tokens and one-time codes often enough that
    a workflow file must never store them.
    """
    if not value.startswith("/") or value.startswith("//") or any(c in value for c in "?#"):
        raise ValueError(
            "must be a path only, starting with '/', with no host, query string, or fragment"
        )
    return value


HtmlTag = Annotated[ShortText, AfterValidator(check_tag)]
HrefPath = Annotated[LongText, AfterValidator(check_href_path)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class FingerprintAttributes(DomainModel):
    """The allowlisted HTML attributes of a target; nothing else is ever stored."""

    id: Text | None = None
    name: Text | None = None
    type: ShortText | None = None
    autocomplete: Text | None = None
    placeholder: Text | None = None
    aria_label: Text | None = None
    """The ``aria-label`` attribute."""
    data_testid: Text | None = None
    """The ``data-testid`` attribute."""
    href: HrefPath | None = None
    """The link's path only, resolved against the page URL."""


class NormalizedBox(DomainModel):
    """A position relative to the whole document (not the viewport), so it is scroll-independent."""

    x: UnitInterval
    y: UnitInterval
    width: UnitInterval
    height: UnitInterval

    @model_validator(mode="after")
    def _stay_inside_the_document(self) -> Self:
        if math.fsum((self.x, self.width)) > 1.0 + _EDGE_TOLERANCE:
            raise ValueError("x + width must not exceed 1")
        if math.fsum((self.y, self.height)) > 1.0 + _EDGE_TOLERANCE:
            raise ValueError("y + height must not exceed 1")
        return self


class Fingerprint(DomainModel):
    """A target element, described redundantly so a heal can match it on any surviving clue."""

    tag: HtmlTag
    role: AriaRole | None = None
    accessible_name: Text | None = None
    text: LongText | None = None
    """Visible text content, whitespace-normalized."""
    label_text: Text | None = None
    attributes: FingerprintAttributes = FingerprintAttributes()
    nearby_text: tuple[Text, ...] = Field(default=(), max_length=NEARBY_TEXT_MAX_ITEMS)
    """Nearest heading, preceding label, and row or column headers."""
    structural_path: LongText
    """Simplified ancestor chain, such as 'main > section > form > button'."""
    bbox: NormalizedBox | None = None
    selectors: tuple[Selector, ...] = Field(min_length=1, max_length=SELECTORS_MAX_ITEMS)
    """Ranked best first; each must find exactly one element."""

    @field_validator("selectors")
    @classmethod
    def _reject_duplicate_selectors(cls, selectors: tuple[Selector, ...]) -> tuple[Selector, ...]:
        for index, selector in enumerate(selectors):
            first = selectors.index(selector)
            if first != index:
                raise ValueError(f"selectors[{index}] duplicates selectors[{first}]")
        return selectors
