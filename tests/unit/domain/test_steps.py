"""Each action has exactly its own shape, enforced by its type."""

from typing import Any

import pytest

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.steps import (
    ClickStep,
    FillStep,
    NavigateStep,
    PressStep,
    SelectStep,
    step_target,
    step_value,
)
from tests.workflows import (
    click_step,
    document,
    field_fingerprint,
    fill_step,
    fingerprint,
    literal,
    navigate_step,
    problems,
    version,
)


def select_step(**overrides: object) -> dict[str, Any]:
    return {
        "id": "choose_region",
        "intent": "Choose 'Europe' in the Region list",
        "action": "select",
        "risk": "caution",
        "target": field_fingerprint(tag="select", role="combobox"),
        "value": literal("Europe"),
        **overrides,
    }


def test_every_action_parses_into_its_own_type() -> None:
    parsed = version(
        steps=[
            navigate_step(),
            click_step(),
            fill_step(),
            select_step(),
            {
                "id": "confirm",
                "intent": "Press Enter",
                "action": "press",
                "risk": "safe",
                "key": "Enter",
            },
        ]
    )

    assert [type(step) for step in parsed.steps] == [
        NavigateStep,
        ClickStep,
        FillStep,
        SelectStep,
        PressStep,
    ]
    assert [step.action for step in parsed.steps] == list(ActionType)
    assert [step_target(step) is not None for step in parsed.steps] == [
        False,
        True,
        True,
        True,
        False,
    ]
    assert [step_value(step) is not None for step in parsed.steps] == [
        True,
        False,
        True,
        True,
        False,
    ]


@pytest.mark.parametrize(
    ("step", "path", "message"),
    [
        (
            navigate_step(target=fingerprint()),
            "steps[0].target",
            "'target' is not a field of a navigate step; allowed: id, intent, action, risk, "
            "value, checkpoints",
        ),
        (
            {k: v for k, v in navigate_step().items() if k != "value"},
            "steps[0].value",
            "is required",
        ),
        (
            click_step(value=literal("x")),
            "steps[0].value",
            "'value' is not a field of a click step; allowed: id, intent, action, risk, target, "
            "checkpoints",
        ),
        (
            {k: v for k, v in click_step().items() if k != "target"},
            "steps[0].target",
            "is required",
        ),
        ({k: v for k, v in fill_step().items() if k != "value"}, "steps[0].value", "is required"),
        ({k: v for k, v in fill_step().items() if k != "target"}, "steps[0].target", "is required"),
        (select_step(value=None), "steps[0].value", "must be a mapping of fields"),
        (
            {
                "id": "go",
                "intent": "Press",
                "action": "press",
                "risk": "safe",
                "key": "Enter",
                "value": literal("x"),
            },
            "steps[0].value",
            "'value' is not a field of a press step; allowed: id, intent, action, risk, target, "
            "key, checkpoints",
        ),
        (
            click_step(action="download"),
            "steps[0]",
            "action must be one of: navigate, click, fill, select, press (got 'download')",
        ),
        (
            {k: v for k, v in click_step().items() if k != "action"},
            "steps[0]",
            "needs a 'action' field",
        ),
        (
            click_step(risk="dangerous"),
            "steps[0].risk",
            "must be one of: 'safe', 'caution' or 'irreversible'",
        ),
        (click_step(intent="   "), "steps[0].intent", "must not be blank"),
        (click_step(ide="save"), "steps[0].ide", "unknown field 'ide'; did you mean 'id'?"),
    ],
)
def test_shape_mistakes_point_at_the_offending_field(
    step: dict[str, Any], path: str, message: str
) -> None:
    assert problems(document(steps=[step])) == [(path, message)]


def test_a_misspelled_action_key_gets_a_suggestion() -> None:
    step = {("acton" if key == "action" else key): value for key, value in click_step().items()}

    assert problems(document(steps=[step])) == [
        ("steps[0]", "needs a 'action' field; found 'acton', did you mean 'action'?")
    ]


def test_a_workflow_needs_at_least_one_step_and_at_most_500() -> None:
    assert problems(document(steps=[])) == [("steps", "must have at least 1 item(s)")]
    many = [click_step(f"step_{n}") for n in range(501)]
    assert problems(document(steps=many)) == [("steps", "must have at most 500 items")]
