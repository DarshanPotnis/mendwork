"""Selectors are ranked, unique, plain, and scoped at most two levels deep."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from mendwork.engine.domain.selectors import ByRole, ByTestId, ByText, Selector, scope_chain
from mendwork.engine.domain.steps import step_target
from tests.workflows import click_step, document, fingerprint, problems, version

SELECTOR: TypeAdapter[Selector] = TypeAdapter(Selector)


def scoped(depth: int) -> dict[str, Any]:
    current: dict[str, Any] = {"strategy": "role_name", "role": "table", "name": "Orders"}
    for level in range(depth - 1):
        current = {
            "strategy": "role_name",
            "role": "row",
            "name": f"row {level}",
            "within": current,
        }
    return (
        {"strategy": "role_name", "role": "button", "name": "View", "within": current}
        if depth
        else current
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"strategy": "test_id", "value": "save"},
        {"strategy": "role_name", "role": "button", "name": "Save"},
        {"strategy": "role_name", "role": "button", "name": "save", "exact": False},
        {"strategy": "label", "value": "Email address"},
        {"strategy": "placeholder", "value": "you@example.com"},
        {"strategy": "text", "value": "Sign in"},
        {"strategy": "css", "value": "form#login > button[type=submit]"},
        {"strategy": "css", "value": "tr:nth-child(2) td:first-child a[href^='/orders']"},
    ],
)
def test_every_strategy_is_accepted(raw: dict[str, Any]) -> None:
    assert SELECTOR.validate_python(raw).strategy == raw["strategy"]


def test_exact_matching_is_the_default() -> None:
    parsed = SELECTOR.validate_python({"strategy": "text", "value": "Save"})

    assert isinstance(parsed, ByText)
    assert parsed.exact is True


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_scopes_up_to_two_levels_are_accepted(depth: int) -> None:
    parsed = SELECTOR.validate_python(scoped(depth))

    assert len(scope_chain(parsed)) == depth + 1


def test_scope_chain_lists_the_outermost_scope_first() -> None:
    parsed = SELECTOR.validate_python(
        {
            "strategy": "role_name",
            "role": "button",
            "name": "View",
            "within": {"strategy": "role_name", "role": "row", "name": "PO-1042", "exact": False},
        }
    )

    chain = scope_chain(parsed)

    assert [type(level) for level in chain] == [ByRole, ByRole]
    assert [level.name for level in chain if isinstance(level, ByRole)] == ["PO-1042", "View"]


def test_three_levels_of_scope_are_rejected() -> None:
    three = {"strategy": "test_id", "value": "view", "within": scoped(2)}

    with pytest.raises(ValidationError, match="'within' nests 3 levels deep; at most 2"):
        SELECTOR.validate_python(three)


@pytest.mark.parametrize(
    "css",
    [
        "button >> text=Save",
        "text=Save",
        "xpath=//button",
        "//button",
        "..",
        "button:has-text('Save')",
        "button:text-is('Save')",
        "button:visible",
        ":nth-match(button, 2)",
        "button:near(#email)",
        "internal:role=button",
    ],
)
def test_playwright_selector_extensions_are_not_css(css: str) -> None:
    with pytest.raises(ValidationError, match="must be plain CSS"):
        SELECTOR.validate_python({"strategy": "css", "value": css})


def test_an_unknown_role_is_rejected_with_the_allowed_roles() -> None:
    target = fingerprint(selectors=[{"strategy": "role_name", "role": "clicky", "name": "Save"}])

    [(path, message)] = problems(document(steps=[click_step(target=target)]))

    assert path == "steps[0].target.selectors[0].role"
    assert message.startswith("must be one of: 'alert', 'alertdialog'")


def test_selector_lists_must_not_be_empty_or_repeat() -> None:
    empty = fingerprint(selectors=[])
    repeated = fingerprint(selectors=[{"strategy": "test_id", "value": "a"}] * 2)

    assert problems(document(steps=[click_step(target=empty)])) == [
        ("steps[0].target.selectors", "must have at least 1 item(s)")
    ]
    assert problems(document(steps=[click_step(target=repeated)])) == [
        ("steps[0].target.selectors", "selectors[1] duplicates selectors[0]")
    ]


def test_selectors_that_differ_only_in_scope_are_distinct() -> None:
    plain = {"strategy": "test_id", "value": "view"}
    within = {**plain, "within": {"strategy": "test_id", "value": "row"}}

    parsed = version(steps=[click_step(target=fingerprint(selectors=[plain, within]))])

    target = step_target(parsed.steps[0])
    assert target is not None
    assert len(target.selectors) == 2


def test_a_blank_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        ByTestId.model_validate({"strategy": "test_id", "value": "   "})
