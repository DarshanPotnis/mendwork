"""Building a recorded target's Fingerprint from page facts and its verified selectors.

Every value is fitted to the format's bounds rather than rejected: an over-long nearby
text or label is dropped, a structural path keeps its nearest levels, and the box is
rounded to four decimals and clamped inside the document. Identity fields are the
exception: an accessible name the format cannot hold would make the Rung 0 identity
check fail, so it raises instead.
"""

from collections.abc import Sequence
from typing import Final

from pydantic import TypeAdapter, ValidationError

from mendwork.engine.domain.base import LongText, Text
from mendwork.engine.domain.enums import AriaRole
from mendwork.engine.domain.fingerprint import (
    Fingerprint,
    FingerprintAttributes,
    HrefPath,
    NormalizedBox,
)
from mendwork.engine.domain.limits import (
    LONG_TEXT_MAX_LENGTH,
    NEARBY_TEXT_MAX_ITEMS,
    SHORT_TEXT_MAX_LENGTH,
)
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.ports.recording_types import Box, ElementFacts

_TEXT: Final[TypeAdapter[str]] = TypeAdapter(Text)
_LONG_TEXT: Final[TypeAdapter[str]] = TypeAdapter(LongText)
_HREF: Final[TypeAdapter[str]] = TypeAdapter(HrefPath)
_DECIMALS: Final = 4


def build_fingerprint(
    facts: ElementFacts, identity: ElementIdentity, selectors: Sequence[Selector]
) -> Fingerprint:
    """The fingerprint for a recorded target. Raises ValidationError if it cannot be stored."""
    role = _role(identity)
    return Fingerprint(
        tag=facts.tag,
        role=role,
        accessible_name=identity.name or None,
        text=_fit(_LONG_TEXT, facts.text),
        label_text=_fit(_TEXT, facts.label_text),
        attributes=_attributes(facts),
        nearby_text=_nearby(facts.nearby_text),
        structural_path=_path(facts.structural_path),
        bbox=normalized_box(facts.box),
        selectors=tuple(selectors),
    )


def normalized_box(box: Box | None) -> NormalizedBox | None:
    """A box rounded to four decimals and clamped inside the document, or None if empty."""
    if box is None:
        return None
    x = _clamp(round(box.x, _DECIMALS))
    y = _clamp(round(box.y, _DECIMALS))
    width = min(_clamp(round(box.width, _DECIMALS)), round(1.0 - x, _DECIMALS))
    height = min(_clamp(round(box.height, _DECIMALS)), round(1.0 - y, _DECIMALS))
    if width <= 0 or height <= 0:
        return None
    return NormalizedBox(x=x, y=y, width=width, height=height)


def _role(identity: ElementIdentity) -> AriaRole | None:
    if identity.role is None:
        return None
    try:
        return AriaRole(identity.role)
    except ValueError:
        return None


def _attributes(facts: ElementFacts) -> FingerprintAttributes:
    kind = facts.type.lower() if facts.type else None
    return FingerprintAttributes(
        id=_fit(_TEXT, facts.id),
        name=_fit(_TEXT, facts.name),
        type=kind if kind is not None and len(kind) <= SHORT_TEXT_MAX_LENGTH else None,
        autocomplete=_fit(_TEXT, facts.autocomplete),
        placeholder=_fit(_TEXT, facts.placeholder),
        aria_label=_fit(_TEXT, facts.aria_label),
        data_testid=_fit(_TEXT, facts.data_testid),
        href=_fit(_HREF, facts.href),
    )


def _nearby(texts: Sequence[str]) -> tuple[str, ...]:
    kept: list[str] = []
    for text in texts:
        fitted = _fit(_TEXT, " ".join(text.split()))
        if fitted is not None and fitted not in kept:
            kept.append(fitted)
        if len(kept) == NEARBY_TEXT_MAX_ITEMS:
            break
    return tuple(kept)


def _path(path: str) -> str:
    levels = [level for level in path.split(" > ") if level]
    while levels and len(" > ".join(levels)) > LONG_TEXT_MAX_LENGTH:
        levels.pop(0)
    return " > ".join(levels) or "element"


def _fit(adapter: TypeAdapter[str], value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return adapter.validate_python(value)
    except ValidationError:
        return None


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
