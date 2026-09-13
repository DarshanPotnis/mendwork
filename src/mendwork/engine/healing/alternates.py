"""Rung 1's alternate selectors: ways to find the recorded identity that were not recorded.

A recording keeps only the selectors that were unique at record time, and a hand-written
fingerprint may keep fewer. Every clue the fingerprint holds can still name the element:
its test id, its role and full name, its role and visible text as a substring, its label,
placeholder, and text, and a stable id or name attribute. Each is also tried inside every
scope the recorded selectors used, because a control that became ambiguous on the page is
often still unique within its row or section. Selectors already recorded are left out:
Rung 0 has evaluated them.
"""

from typing import Final

from pydantic import ValidationError

from mendwork.engine.domain.enums import SelectorStrategy
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.recording.selectors import (
    SELECTOR,
    TEXT_SELECTOR_MAX_LENGTH,
    css_selector,
    with_scope,
)

_LABELABLE: Final = frozenset({"input", "select", "textarea"})


def alternate_selectors(fingerprint: Fingerprint) -> tuple[Selector, ...]:
    """Every selector derived from the fingerprint that it did not record, best first."""
    base = _unique(_documents(fingerprint))
    scopes = _unique_selectors(
        [selector.within for selector in fingerprint.selectors if selector.within is not None]
    )
    scoped = [
        found
        for scope in scopes
        for selector in base
        if (found := with_scope(selector, scope)) is not None
    ]
    return tuple(
        selector
        for selector in _unique_selectors([*base, *scoped])
        if selector not in fingerprint.selectors
    )


def _documents(fingerprint: Fingerprint) -> list[dict[str, object]]:
    attributes = fingerprint.attributes
    name = _flat(fingerprint.accessible_name)
    text = _flat(fingerprint.text)
    documents: list[dict[str, object]] = []
    if attributes.data_testid:
        documents.append({"strategy": SelectorStrategy.TEST_ID, "value": attributes.data_testid})
    if fingerprint.role is not None and name:
        role = fingerprint.role
        documents.append({"strategy": SelectorStrategy.ROLE_NAME, "role": role, "name": name})
        if text and text.casefold() != name.casefold():
            documents.append(
                {"strategy": SelectorStrategy.ROLE_NAME, "role": role, "name": text, "exact": False}
            )
    if fingerprint.label_text and fingerprint.tag in _LABELABLE:
        documents.append(
            {"strategy": SelectorStrategy.LABEL, "value": _flat(fingerprint.label_text)}
        )
    if attributes.placeholder:
        documents.append(
            {"strategy": SelectorStrategy.PLACEHOLDER, "value": attributes.placeholder}
        )
    if text and fingerprint.tag not in _LABELABLE and len(text) <= TEXT_SELECTOR_MAX_LENGTH:
        documents.append({"strategy": SelectorStrategy.TEXT, "value": text})
    css = css_selector(fingerprint.tag, attributes.id, attributes.name)
    if css is not None:
        documents.append({"strategy": SelectorStrategy.CSS, "value": css})
    return documents


def _unique(documents: list[dict[str, object]]) -> list[Selector]:
    selectors: list[Selector] = []
    for document in documents:
        try:
            selectors.append(SELECTOR.validate_python(document))
        except ValidationError:
            continue
    return _unique_selectors(selectors)


def _unique_selectors(selectors: list[Selector]) -> list[Selector]:
    kept: list[Selector] = []
    for selector in selectors:
        if selector not in kept:
            kept.append(selector)
    return kept


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())
