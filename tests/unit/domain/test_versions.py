"""Version lineage: numbering, change records, UTC timestamps, and deriving children."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given

from mendwork.engine.domain.changes import ManualEdit, Rollback, describe_change
from mendwork.engine.domain.lineage import edit_version, roll_back_version
from mendwork.engine.domain.steps import ClickStep
from mendwork.engine.domain.workflow import WorkflowContent, WorkflowVersion
from mendwork.engine.errors import WorkflowValidationError
from tests.fakes.clock import FakeClock
from tests.strategies import text, workflow_versions
from tests.workflows import CREATED_AT_DATETIME, click_step, document, problems, version

EDIT = {"kind": "manual_edit", "summary": "Tightened the sign-in checkpoint"}


def later(**overrides: object) -> dict[str, Any]:
    return document(version=2, parent_version=1, change=EDIT, **overrides)


def test_version_one_has_no_parent_and_no_change() -> None:
    first = version()

    assert (first.version, first.parent_version, first.change) == (1, None, None)


def test_a_later_version_names_its_parent_and_its_reason() -> None:
    second = version(version=2, parent_version=1, change=EDIT)

    assert second.change == ManualEdit.model_validate(EDIT)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"parent_version": 1},
            [("parent_version", "version 1 has no parent; remove parent_version")],
        ),
        ({"change": EDIT}, [("change", "version 1 has no change record; remove change")]),
        (
            {"version": 3, "change": EDIT},
            [("parent_version", "must be 2: a version's parent is the version before it")],
        ),
        (
            {"version": 3, "parent_version": 1, "change": EDIT},
            [("parent_version", "must be 2: a version's parent is the version before it")],
        ),
        (
            {"version": 2, "parent_version": 1},
            [("change", "is required: every version after the first records why it exists")],
        ),
        ({"version": 0}, [("version", "must be at least 1")]),
        (
            {
                "version": 3,
                "parent_version": 2,
                "change": {"kind": "rollback", "restored_version": 2, "reason": "undo"},
            },
            [
                (
                    "change",
                    "a rollback in version 3 can restore at most version 1; restoring the parent "
                    "itself would change nothing",
                )
            ],
        ),
        (
            {"version": 2, "parent_version": 1, "change": {"kind": "heal"}},
            [("change", "kind must be one of: manual_edit, rollback (got 'heal')")],
        ),
    ],
)
def test_lineage_mistakes_are_explained(
    overrides: dict[str, Any], expected: list[tuple[str, str]]
) -> None:
    assert problems(document(**overrides)) == expected


@pytest.mark.parametrize(
    ("created_at", "message"),
    [
        ("2026-09-11T10:30:00+02:00", "must be a UTC timestamp, such as 2026-09-11T08:30:00Z"),
        ("2026-09-11T08:30:00", "must be a UTC timestamp, such as 2026-09-11T08:30:00Z"),
        ("yesterday", "must be a UTC timestamp, such as 2026-09-11T08:30:00Z"),
    ],
)
def test_timestamps_must_be_utc(created_at: str, message: str) -> None:
    assert problems(document(created_at=created_at)) == [("created_at", message)]


def test_describe_change_covers_every_kind() -> None:
    assert (
        describe_change(ManualEdit.model_validate(EDIT))
        == "manual edit: Tightened the sign-in checkpoint"
    )
    rollback = Rollback.model_validate(
        {"kind": "rollback", "restored_version": 2, "reason": "bad heal"}
    )
    assert describe_change(rollback) == "rollback to v2: bad heal"


def renamed(parent: WorkflowVersion, intent: str) -> WorkflowContent:
    steps = tuple(
        step.model_copy(update={"intent": intent}) if isinstance(step, ClickStep) else step
        for step in parent.steps
    )
    return WorkflowContent(inputs=parent.inputs, secrets=parent.secrets, steps=steps)


def test_an_edit_creates_the_next_version_and_leaves_the_parent_untouched() -> None:
    parent = version()
    snapshot = parent.model_copy(deep=True)
    clock = FakeClock(CREATED_AT_DATETIME + timedelta(hours=1))

    child = edit_version(
        parent, renamed(parent, "Click 'Save changes'"), summary="Clearer intent", clock=clock
    )

    assert parent == snapshot
    assert (child.workflow_id, child.version, child.parent_version) == ("demo", 2, 1)
    assert child.created_at == clock.now()
    assert child.change == ManualEdit.model_validate(
        {"kind": "manual_edit", "summary": "Clearer intent"}
    )
    assert [step.id for step in child.steps] == [step.id for step in parent.steps]
    assert child.steps[2].intent == "Click 'Save changes'"


def test_an_edit_that_changes_nothing_is_refused() -> None:
    parent = version()

    with pytest.raises(WorkflowValidationError, match="changes nothing"):
        edit_version(parent, parent.content, summary="No-op", clock=FakeClock(CREATED_AT_DATETIME))


def test_an_edit_must_keep_step_ids_in_order() -> None:
    parent = version()
    reordered = WorkflowContent(steps=tuple(reversed(parent.steps)))

    with pytest.raises(WorkflowValidationError) as caught:
        edit_version(parent, reordered, summary="Reorder", clock=FakeClock(CREATED_AT_DATETIME))

    [issue] = caught.value.issues
    assert issue.path == "steps"
    assert (
        issue.message
        == "step ids must stay ['open_portal', 'fill_name', 'save'], got ['save', 'fill_name', "
        "'open_portal']"
    )


def test_a_clock_that_is_not_utc_is_refused() -> None:
    parent = version()
    clock = FakeClock(datetime(2026, 9, 11, 10, 0, tzinfo=timezone(timedelta(hours=2))))

    with pytest.raises(WorkflowValidationError) as caught:
        edit_version(parent, renamed(parent, "Other"), summary="Edit", clock=clock)

    assert [(issue.path, issue.message) for issue in caught.value.issues] == [
        ("created_at", "must be a UTC timestamp, such as 2026-09-11T08:30:00Z")
    ]


def test_a_blank_summary_is_refused_at_its_path() -> None:
    parent = version()

    with pytest.raises(WorkflowValidationError) as caught:
        edit_version(
            parent, renamed(parent, "Other"), summary=" ", clock=FakeClock(CREATED_AT_DATETIME)
        )

    assert [(issue.path, issue.message) for issue in caught.value.issues] == [
        ("change.summary", "must not be blank")
    ]


def test_a_rollback_restores_earlier_content_as_a_new_version() -> None:
    clock = FakeClock(CREATED_AT_DATETIME)
    first = version()
    second = edit_version(first, renamed(first, "Changed"), summary="Edit", clock=clock)
    clock.advance(timedelta(minutes=5))

    third = roll_back_version(second, first, reason="The edit broke replay", clock=clock)

    assert (third.version, third.parent_version) == (3, 2)
    assert third.content == first.content
    assert third.change == Rollback.model_validate(
        {"kind": "rollback", "restored_version": 1, "reason": "The edit broke replay"}
    )
    assert third.created_at == CREATED_AT_DATETIME + timedelta(minutes=5)


@pytest.mark.parametrize(
    ("restored_overrides", "message"),
    [
        ({"workflow_id": "other"}, "cannot restore other into demo"),
        (
            {"version": 2, "parent_version": 1, "change": EDIT},
            "can only roll back to a version before v2, not v2",
        ),
    ],
)
def test_impossible_rollbacks_are_refused(restored_overrides: dict[str, Any], message: str) -> None:
    clock = FakeClock(CREATED_AT_DATETIME)
    first = version()
    second = edit_version(first, renamed(first, "Changed"), summary="Edit", clock=clock)

    with pytest.raises(WorkflowValidationError, match=message):
        roll_back_version(second, version(**restored_overrides), reason="Undo", clock=clock)


def test_a_rollback_to_identical_content_is_refused() -> None:
    clock = FakeClock(CREATED_AT_DATETIME)
    first = version()
    second = edit_version(first, renamed(first, "Changed"), summary="Edit", clock=clock)
    third = roll_back_version(second, first, reason="Undo", clock=clock)

    with pytest.raises(WorkflowValidationError, match="v3 already has the content of v1"):
        roll_back_version(third, first, reason="Again", clock=clock)


def test_the_utc_timestamp_is_normalised_to_the_utc_zone() -> None:
    assert version(created_at="2026-09-11T08:30:00+00:00").created_at.tzinfo is UTC


def test_content_rejects_inconsistent_references_too() -> None:
    with pytest.raises(ValueError, match="secret 'orphan' is declared but no step uses it"):
        WorkflowContent.model_validate({"secrets": ["orphan"], "steps": [click_step()]})


@given(workflow_versions(), text())
def test_every_edit_keeps_lineage_invariants(parent: WorkflowVersion, intent: str) -> None:
    first = parent.steps[0]
    changed = first.model_copy(update={"intent": intent + " (edited)"})
    content = WorkflowContent(
        inputs=parent.inputs, secrets=parent.secrets, steps=(changed, *parent.steps[1:])
    )
    snapshot = parent.model_copy(deep=True)

    child = edit_version(parent, content, summary="Edited", clock=FakeClock(CREATED_AT_DATETIME))

    assert parent == snapshot
    assert (child.version, child.parent_version) == (parent.version + 1, parent.version)
    assert [step.id for step in child.steps] == [step.id for step in parent.steps]
    assert child.content == content
