"""Translating pydantic validation errors into issues people can act on.

Pydantic reports locations in its own terms, including discriminated-union tags that are
not keys in the document, and messages written for programmers. This module maps each
error back onto the document's real path and rewrites the common cases in plain English.
Messages never echo input values: a mistyped field may hold a password.
"""

import re
import types
from collections.abc import Mapping
from difflib import get_close_matches
from typing import Annotated, Final, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from mendwork.engine.domain.identifiers import SLUG_DESCRIPTION, SLUG_PATTERN
from mendwork.engine.domain.workflow import REFERENCE_ERROR_TYPE, WorkflowVersion
from mendwork.engine.errors import Location, ValidationIssue

_DISCRIMINATORS: Final = ("kind", "strategy", "action")
_TAG_IN_CONTEXT: Final = re.compile(r"'([^']*)'")
_QUOTED_TAG_LIMIT: Final = 40
_ACTIONS: Final = frozenset({"navigate", "click", "fill", "select", "press"})
_MISSING: Final = object()

_PLAIN_MESSAGES: Final[Mapping[str, str]] = {
    "missing": "is required",
    "bool_type": "must be true or false",
    "int_type": "must be a whole number",
    "int_from_float": "must be a whole number",
    "float_type": "must be a number",
    "finite_number": "must be a finite number",
    "tuple_type": "must be a list",
    "list_type": "must be a list",
    "model_type": "must be a mapping of fields",
    "model_attributes_type": "must be a mapping of fields",
    "dict_type": "must be a mapping of fields",
    "datetime_type": "must be a UTC timestamp, such as 2026-09-11T08:30:00Z",
    "datetime_parsing": "must be a UTC timestamp, such as 2026-09-11T08:30:00Z",
    "none_required": "must be empty (null)",
}


def issues_from_validation_error(
    error: ValidationError, document: object
) -> tuple[ValidationIssue, ...]:
    """Every error in a failed validation, located in the document and explained."""
    issues: list[ValidationIssue] = []
    details = error.errors(include_url=False)
    for detail in details:
        if _is_consequence_of_item_errors(detail, details):
            continue
        if detail["type"] == REFERENCE_ERROR_TYPE:
            issues.extend(_reference_issues(detail))
            continue
        location = document_location(detail["loc"], document)
        issues.append(ValidationIssue(location, _message(detail, document)))
    return tuple(issues)


def _is_consequence_of_item_errors(detail: ErrorDetails, details: list[ErrorDetails]) -> bool:
    # Pydantic counts only the items that validated, so a list whose items all failed is
    # also reported as too short; that second report would send a reader the wrong way.
    if detail["type"] not in {"too_short", "too_long"}:
        return False
    loc = detail["loc"]
    return any(
        len(other["loc"]) > len(loc) and other["loc"][: len(loc)] == loc for other in details
    )


def _reference_issues(detail: ErrorDetails) -> tuple[ValidationIssue, ...]:
    carried = detail.get("ctx", {}).get("issues", ())
    if not isinstance(carried, tuple) or not all(isinstance(i, ValidationIssue) for i in carried):
        raise TypeError(f"{REFERENCE_ERROR_TYPE} errors must carry a tuple of ValidationIssue")
    return carried


def _child(node: object, part: str | int) -> object:
    if isinstance(node, dict) and isinstance(part, str) and part in node:
        child: object = node[part]
        return child
    if isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node):
        item: object = node[part]
        return item
    return _MISSING


def document_location(loc: tuple[str | int, ...], document: object) -> Location:
    """Map a pydantic error location onto the document, dropping union-tag segments."""
    path: list[str | int] = []
    node = document
    for index, part in enumerate(loc):
        child = _child(node, part)
        if child is not _MISSING:
            node = child
        elif isinstance(node, dict) and any(node.get(key) == part for key in _DISCRIMINATORS):
            continue
        else:
            path.extend(loc[index:])
            break
        path.append(part)
    return tuple(path)


def _message(detail: ErrorDetails, document: object) -> str:
    kind = detail["type"]
    context = detail.get("ctx", {})
    loc = detail["loc"]
    if kind in _PLAIN_MESSAGES:
        return _PLAIN_MESSAGES[kind]
    match kind:
        case "extra_forbidden":
            return _unknown_field_message(loc)
        case "union_tag_invalid":
            return _invalid_tag_message(context, loc)
        case "union_tag_not_found":
            return _missing_tag_message(context, loc, document)
        case "literal_error" | "enum":
            return f"must be one of: {context.get('expected')}"
        case "string_type":
            return "must be text" + _yaml_type_hint(detail.get("input"))
        case "int_parsing" | "float_parsing" | "bool_parsing":
            return _PLAIN_MESSAGES[kind.replace("parsing", "type")]
        case "string_too_short":
            return "must not be empty"
        case "string_too_long":
            return f"must be at most {context.get('max_length')} characters"
        case "too_short":
            return f"must have at least {context.get('min_length')} item(s)"
        case "too_long":
            return f"must have at most {context.get('max_length')} items"
        case "string_pattern_mismatch":
            if context.get("pattern") == SLUG_PATTERN:
                return SLUG_DESCRIPTION
            return f"must match the pattern {context.get('pattern')}"
        case "greater_than_equal" | "greater_than" | "less_than_equal" | "less_than":
            return _bound_message(kind, context)
        case "value_error" | "assertion_error":
            return str(context.get("error", detail["msg"]))
        case _:
            return detail["msg"]


def _bound_message(kind: str, context: Mapping[str, object]) -> str:
    words = {
        "greater_than_equal": ("at least", "ge"),
        "greater_than": ("greater than", "gt"),
        "less_than_equal": ("at most", "le"),
        "less_than": ("less than", "lt"),
    }
    phrase, key = words[kind]
    return f"must be {phrase} {context.get(key)}"


def _yaml_type_hint(value: object) -> str:
    if isinstance(value, bool):
        return "; YAML read this as true/false, so put the value in quotes"
    if isinstance(value, int | float):
        return "; YAML read this as a number, so put the value in quotes"
    return ""


def _invalid_tag_message(context: Mapping[str, object], loc: tuple[str | int, ...]) -> str:
    discriminator = str(context.get("discriminator", "")).strip("'")
    expected = ", ".join(_TAG_IN_CONTEXT.findall(str(context.get("expected_tags", ""))))
    tag = str(context.get("tag", ""))[:_QUOTED_TAG_LIMIT]
    message = f"{discriminator} must be one of: {expected} (got '{tag}')"
    if discriminator == "kind" and tag == "secret" and "value" in loc:
        message += "; secret references are only allowed in fill steps"
    return message


def _missing_tag_message(
    context: Mapping[str, object], loc: tuple[str | int, ...], document: object
) -> str:
    discriminator = str(context.get("discriminator", "")).strip("'")
    message = f"needs a '{discriminator}' field"
    node = _node_at(document_location(loc, document), document)
    if isinstance(node, dict):
        closest = get_close_matches(discriminator, [str(key) for key in node], n=1)
        if closest:
            message += f"; found '{closest[0]}', did you mean '{discriminator}'?"
    return message


def _unknown_field_message(loc: tuple[str | int, ...]) -> str:
    field = str(loc[-1])
    allowed = _fields_at(loc[:-1])
    if allowed:
        closest = get_close_matches(field, allowed, n=1)
        if closest:
            return f"unknown field '{field}'; did you mean '{closest[0]}'?"
        return f"'{field}' is not a field of {_subject(loc[:-1])}; allowed: {', '.join(allowed)}"
    return f"unknown field '{field}'"


def _subject(loc: tuple[str | int, ...]) -> str:
    # The innermost union tag names what the field was written on, such as "a click step".
    tag = loc[-1] if loc else None
    if len(loc) == 3 and loc[0] == "steps" and tag in _ACTIONS:
        return f"a {tag} step"
    if isinstance(tag, str) and _fields_at(loc) and not _fields_at(loc[:-1]):
        return f"'{tag}'"
    return "this mapping"


def _node_at(location: Location, document: object) -> object:
    node = document
    for part in location:
        node = _child(node, part)
    return node


def _members(annotation: object) -> list[object]:
    if get_origin(annotation) is Annotated:
        return _members(get_args(annotation)[0])
    if get_origin(annotation) in (Union, types.UnionType):
        return [member for arg in get_args(annotation) for member in _members(arg)]
    return [annotation]


def _step_into(annotation: object, part: str | int) -> object | None:
    for member in _members(annotation):
        if get_origin(member) is tuple and isinstance(part, int):
            item: object = get_args(member)[0]
            return item
        if isinstance(member, type) and issubclass(member, BaseModel) and isinstance(part, str):
            field = member.model_fields.get(part)
            if field is not None:
                return field.annotation
            if _has_tag(member, part):
                return member
    return None


def _has_tag(model: type[BaseModel], tag: str) -> bool:
    for name in _DISCRIMINATORS:
        field = model.model_fields.get(name)
        if field is not None and any(str(value) == tag for value in get_args(field.annotation)):
            return True
    return False


def _fields_at(loc: tuple[str | int, ...]) -> list[str]:
    annotation: object | None = WorkflowVersion
    for part in loc:
        annotation = _step_into(annotation, part)
        if annotation is None:
            return []
    models = [m for m in _members(annotation) if isinstance(m, type) and issubclass(m, BaseModel)]
    return list(models[0].model_fields) if len(models) == 1 else []
