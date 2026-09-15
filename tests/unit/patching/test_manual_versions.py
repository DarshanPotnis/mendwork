"""Planning an import and a rollback: what each publishes, undoes, and leaves behind (ADR 0013)."""

from datetime import timedelta
from typing import Final

import pytest

from mendwork.engine.domain.changes import ManualEdit, Rollback
from mendwork.engine.domain.enums import ChangeKind
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.lineage import edit_version, heal_version
from mendwork.engine.domain.patches import PendingPatch
from mendwork.engine.domain.run_identifiers import parse_run_id
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import UnknownWorkflowVersion, WorkflowValidationError
from mendwork.engine.patching.manual_versions import (
    AlreadyLatest,
    ImportPlan,
    plan_import,
    plan_rollback,
    stored_version,
)
from mendwork.engine.patching.promotion import started
from tests.fakes.clock import FakeClock
from tests.heal_changes import heal_change, renamed_target, target_of
from tests.unit.patching.builders import with_intent
from tests.workflows import CREATED_AT_DATETIME, click_step, fill_step, navigate_step, version

CLOCK: Final = FakeClock(CREATED_AT_DATETIME + timedelta(days=2))
RUN: Final = parse_run_id("20260915T100000Z-00000001")


def lineage() -> tuple[WorkflowVersion, WorkflowVersion, WorkflowVersion]:
    """v1; v2, whose save step a heal retargeted; v3, whose fill_name intent was edited by hand."""
    first = version()
    second = heal_version(first, heal_change(first), clock=CLOCK)
    third = edit_version(
        second,
        with_intent(second, "fill_name", "Type the full name"),
        summary="clearer",
        clock=CLOCK,
    )
    return first, second, third


def pending_on(workflow: WorkflowVersion, step_id: str, name: str) -> PendingPatch:
    """A pending heal of one of the version's steps, to an element with another name."""
    new_target = renamed_target(target_of(workflow, step_id), name)
    change = heal_change(workflow, step_id, new_target=new_target)
    step = next(item for item in workflow.steps if item.id == step_id)
    return started(workflow.workflow_id, step, change, RUN, CLOCK.now())


def test_importing_into_an_empty_store_stores_the_file_as_the_first_version() -> None:
    file = version()

    plan = plan_import(file, (), (), summary="imported", clock=CLOCK)

    assert plan == ImportPlan(version=file, latest=None, diff=None, stops_matching=(), same_as=None)


def test_importing_the_latest_version_s_content_changes_nothing() -> None:
    first, second, _ = lineage()
    same_content = second.model_copy(update={"created_at": CLOCK.now()})

    plan = plan_import(same_content, (first, second), (), summary="again", clock=CLOCK)

    assert plan == AlreadyLatest(version=2)


def test_an_import_follows_the_latest_version_and_names_the_pending_patches_it_strands() -> None:
    first, second, third = lineage()
    stranded = pending_on(third, "save", "Store")
    kept = pending_on(third, "fill_name", "Name")
    file = third.model_copy(update={"steps": with_intent(third, "save", "Save the form").steps})

    plan = plan_import(
        file,
        (first, second, third),
        (stranded, kept),
        summary="Imported from demo.yaml",
        clock=CLOCK,
    )

    assert isinstance(plan, ImportPlan)
    assert (plan.version.version, plan.version.parent_version, plan.latest) == (4, 3, third)
    assert plan.version.change == ManualEdit(
        kind=ChangeKind.MANUAL_EDIT, summary="Imported from demo.yaml"
    )
    assert plan.version.content == file.content
    assert plan.diff is not None
    assert [step.step_id for step in plan.diff.steps] == ["save"]
    assert [(line.step_id, line.target) for line in plan.stops_matching] == [
        ("save", 'a button named "Store"')
    ]
    assert plan.same_as is None


def test_importing_an_earlier_version_s_content_says_which_version_it_was() -> None:
    first, second, third = lineage()

    plan = plan_import(first, (first, second, third), (), summary="back", clock=CLOCK)

    assert isinstance(plan, ImportPlan)
    assert (plan.version.version, plan.version.content, plan.same_as) == (4, first.content, 1)


def test_a_file_whose_step_ids_differ_cannot_be_imported() -> None:
    first = version()
    renamed = version(steps=[navigate_step(), fill_step(), click_step("submit")])

    with pytest.raises(WorkflowValidationError, match="same step ids"):
        plan_import(renamed, (first,), (), summary="renamed", clock=CLOCK)


def test_a_rollback_follows_the_latest_version_and_lists_everything_it_undoes() -> None:
    first, second, third = lineage()
    stranded = pending_on(third, "save", "Store")

    plan = plan_rollback(
        third.workflow_id,
        (first, second, third),
        1,
        (stranded,),
        reason="the wrong button",
        clock=CLOCK,
    )

    assert (plan.version.version, plan.version.parent_version) == (4, 3)
    assert plan.version.change == Rollback(
        kind=ChangeKind.ROLLBACK, restored_version=1, reason="the wrong button"
    )
    assert plan.version.content == first.content
    assert (plan.latest, plan.restored) == (third, first)
    assert [entry.version for entry in plan.undone] == [3, 2]
    assert plan.undoes_heals is True
    assert [step.step_id for step in plan.diff.steps] == ["fill_name", "save"]
    assert [line.step_id for line in plan.stops_matching] == ["save"]


def test_rolling_back_past_only_a_hand_edit_undoes_no_heal() -> None:
    first, second, third = lineage()

    plan = plan_rollback(third.workflow_id, (first, second, third), 2, (), reason="r", clock=CLOCK)

    assert [entry.version for entry in plan.undone] == [3]
    assert plan.undoes_heals is False
    assert [step.step_id for step in plan.diff.steps] == ["fill_name"]


def test_a_version_that_is_not_stored_is_named_with_the_versions_that_are() -> None:
    first, second, third = lineage()
    history = (first, second, third)

    with pytest.raises(UnknownWorkflowVersion, match="demo has no v9; its versions are v1 to v3"):
        plan_rollback(third.workflow_id, history, 9, (), reason="r", clock=CLOCK)
    with pytest.raises(UnknownWorkflowVersion, match="the workflow store has no versions of demo"):
        stored_version(WorkflowId("demo"), (), 1)
    assert stored_version(third.workflow_id, history, 2) == second


def test_the_latest_version_cannot_be_rolled_back_to() -> None:
    first, second, third = lineage()

    with pytest.raises(WorkflowValidationError, match="before v3, not v3"):
        plan_rollback(third.workflow_id, (first, second, third), 3, (), reason="r", clock=CLOCK)
