"""Candidate selectors and scopes for a recorded target: pure, ranked, and unverified.

Candidates are ranked test_id > role_name (the full name, then the element's own text as a
substring) > label > placeholder > text > css. Nothing here decides that a candidate works;
the recorder keeps only those that find exactly the recorded element in the browser.

The own-text candidate exists for repeated controls whose names differ only by text meant
for screen readers ("Open" plus a hidden " invoice INV-2231"). Its short text matches every
row's control, which is what makes the recorder scope it to the one row.
"""

import re
from collections.abc import Sequence
from typing import Final

from pydantic import TypeAdapter, ValidationError

from mendwork.engine.domain.enums import AriaRole, SelectorStrategy
from mendwork.engine.domain.selectors import (
    ByCss,
    ByLabel,
    ByPlaceholder,
    ByRole,
    ByTestId,
    ByText,
    Selector,
)
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.ports.element_types import ElementFacts
from mendwork.engine.ports.recording_types import AncestorFacts

SELECTOR: Final[TypeAdapter[Selector]] = TypeAdapter(Selector)
TEXT_SELECTOR_MAX_LENGTH: Final = 128
_LABELABLE: Final = frozenset({"input", "select", "textarea"})
_NAMED_ATTRIBUTE_TAGS: Final = frozenset({"button", "input", "select", "textarea"})
_CSS_IDENTIFIER: Final = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_NAME_ATTRIBUTE: Final = re.compile(r"[A-Za-z0-9_.:\[\]-]+")
# Ids that frameworks generate change between builds or page loads.
_GENERATED_ID: Final = re.compile(r":|\d{5,}|[0-9a-f]{8,}", re.IGNORECASE)
_SCOPE_ROLES: Final = frozenset(
    {
        "alertdialog",
        "article",
        "banner",
        "complementary",
        "contentinfo",
        "dialog",
        "form",
        "grid",
        "group",
        "list",
        "listitem",
        "main",
        "menu",
        "navigation",
        "region",
        "search",
        "table",
        "tablist",
        "tabpanel",
        "toolbar",
        "treegrid",
    }
)

Scope = tuple[Selector, int]
"""A scope selector and how many levels above the target its element is."""


def candidate_selectors(facts: ElementFacts, identity: ElementIdentity) -> tuple[Selector, ...]:
    """Every selector worth trying for a target, best first, without duplicates."""
    documents: list[dict[str, object]] = []
    if facts.data_testid:
        documents.append({"strategy": SelectorStrategy.TEST_ID, "value": facts.data_testid})
    role = _confirmed_role(identity)
    if role is not None and identity.name:
        documents.append(
            {"strategy": SelectorStrategy.ROLE_NAME, "role": role, "name": identity.name}
        )
        own = _flat(facts.own_text)
        if own and own.casefold() != _flat(identity.name).casefold():
            documents.append(
                {"strategy": SelectorStrategy.ROLE_NAME, "role": role, "name": own, "exact": False}
            )
    if facts.label_text and facts.tag in _LABELABLE:
        documents.append({"strategy": SelectorStrategy.LABEL, "value": _flat(facts.label_text)})
    if facts.placeholder:
        documents.append({"strategy": SelectorStrategy.PLACEHOLDER, "value": facts.placeholder})
    text = _flat(facts.text)
    if text and not facts.text_entry and len(text) <= TEXT_SELECTOR_MAX_LENGTH:
        documents.append({"strategy": SelectorStrategy.TEXT, "value": text})
    css = css_selector(facts.tag, facts.id, facts.name)
    if css is not None:
        documents.append({"strategy": SelectorStrategy.CSS, "value": css})
    found: list[Selector] = []
    for document in documents:
        selector = _valid(document)
        if selector is not None and selector not in found:
            found.append(selector)
    return tuple(found)


def css_selector(tag: str, element_id: str | None, name: str | None) -> str | None:
    """``#id`` for a stable-looking id, else ``tag[name="…"]`` for a named form control."""
    if (
        element_id
        and _CSS_IDENTIFIER.fullmatch(element_id)
        and not _GENERATED_ID.search(element_id)
    ):
        return f"#{element_id}"
    if name and tag in _NAMED_ATTRIBUTE_TAGS and _NAME_ATTRIBUTE.fullmatch(name):
        return f'{tag}[name="{name}"]'
    return None


def scope_selectors(ancestors: Sequence[AncestorFacts]) -> tuple[Scope, ...]:
    """Scopes to try for an ambiguous selector, each with its ancestor's distance.

    Rows by their row header come first, because rows are what repeat identical controls;
    then named containers; then test ids; then stable ids. Within each kind the nearest
    ancestor comes first. Cells are never scopes: their names come from the very content
    being scoped.
    """
    rows: list[Scope] = []
    named: list[Scope] = []
    test_ids: list[Scope] = []
    ids: list[Scope] = []
    for distance, ancestor in enumerate(ancestors, start=1):
        if ancestor.role == AriaRole.ROW and ancestor.row_header:
            _collect(
                rows,
                distance,
                {
                    "strategy": SelectorStrategy.ROLE_NAME,
                    "role": AriaRole.ROW,
                    "name": _flat(ancestor.row_header),
                    "exact": False,
                },
            )
        if ancestor.role in _SCOPE_ROLES and ancestor.name:
            _collect(
                named,
                distance,
                {
                    "strategy": SelectorStrategy.ROLE_NAME,
                    "role": ancestor.role,
                    "name": ancestor.name,
                },
            )
        if ancestor.data_testid:
            _collect(
                test_ids,
                distance,
                {"strategy": SelectorStrategy.TEST_ID, "value": ancestor.data_testid},
            )
        stable = css_selector(ancestor.tag, ancestor.id, None)
        if stable is not None:
            _collect(ids, distance, {"strategy": SelectorStrategy.CSS, "value": stable})
    return tuple(rows + named + test_ids + ids)


def with_scope(selector: Selector, scope: Selector) -> Selector | None:
    """The selector searched inside ``scope``, or None if that would nest too deeply."""
    document = selector.model_dump(mode="json", exclude={"within"})
    document["within"] = scope.model_dump(mode="json")
    return _valid(document)


def summarize(selector: Selector) -> str:
    """A selector in words for people: ``role_name button 'View' (substring) within …``."""
    exact = True
    words = ""
    match selector:
        case ByRole():
            words = f"role_name {selector.role} '{selector.name}'"
            exact = selector.exact
        case ByCss():
            words = f"css {selector.value}"
        case ByTestId():
            words = f"test_id '{selector.value}'"
        case ByLabel() | ByPlaceholder() | ByText():
            words = f"{selector.strategy} '{selector.value}'"
            exact = selector.exact
    if not exact:
        words += " (substring)"
    if selector.within is not None:
        words += f" within {summarize(selector.within)}"
    return words


def _collect(bucket: list[Scope], distance: int, document: dict[str, object]) -> None:
    selector = _valid(document)
    if selector is not None and all(selector != existing for existing, _ in bucket):
        bucket.append((selector, distance))


def _confirmed_role(identity: ElementIdentity) -> AriaRole | None:
    if identity.role is None or identity.confirmed is not True:
        return None
    try:
        return AriaRole(identity.role)
    except ValueError:
        return None


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def _valid(document: dict[str, object]) -> Selector | None:
    try:
        return SELECTOR.validate_python(document)
    except ValidationError:
        return None
