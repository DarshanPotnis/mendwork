"""Placing a verified heal on the latest version, which may be newer than the run's (ADR 0013)."""

from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.lineage import edit_version, heal_version, roll_back_version
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.rebase import Placed, Placement, place, step_named, undoing_rollback
from tests.fakes.clock import FakeClock
from tests.heal_changes import heal_change, renamed_target, target_of
from tests.unit.patching.builders import with_intent
from tests.workflows import CREATED_AT_DATETIME, version

CLOCK: Final = FakeClock(CREATED_AT_DATETIME)


def base() -> tuple[WorkflowVersion, Step, Fingerprint]:
    first = version()
    step = step_named(first, "save")
    assert step is not None
    return first, step, renamed_target(target_of(first, "save"), "Save changes")


def test_a_heal_of_the_latest_version_s_exact_step_is_published() -> None:
    first, step, new = base()

    assert place((first,), step, new) == Placed(Placement.PUBLISH, 1)


def test_a_heal_the_latest_version_already_carries_is_already_applied() -> None:
    first, step, new = base()
    second = heal_version(first, heal_change(first, new_target=new), clock=CLOCK)

    assert place((first, second), step, new) == Placed(Placement.ALREADY_APPLIED, 2)


def test_a_heal_to_another_element_after_a_newer_heal_is_stale() -> None:
    first, step, new = base()
    other = renamed_target(target_of(first, "save"), "Store changes")
    second = heal_version(first, heal_change(first, new_target=other), clock=CLOCK)

    assert place((first, second), step, new) == Placed(Placement.STALE, 2)


def test_a_heal_of_a_step_edited_since_is_stale_even_when_the_edit_kept_its_target() -> None:
    first, step, new = base()
    second = edit_version(
        first, with_intent(first, "save", "Save the form"), summary="e", clock=CLOCK
    )

    assert place((first, second), step, new) == Placed(Placement.STALE, 2)


def test_an_edit_to_another_step_keeps_the_heal_publishable_on_the_newer_version() -> None:
    first, step, new = base()
    second = edit_version(
        first, with_intent(first, "fill_name", "Type the name"), summary="e", clock=CLOCK
    )

    assert place((first, second), step, new) == Placed(Placement.PUBLISH, 2)


def test_a_heal_a_person_rolled_back_is_not_saved_again_but_another_heal_is() -> None:
    first, step, new = base()
    second = heal_version(first, heal_change(first, new_target=new), clock=CLOCK)
    third = roll_back_version(second, first, reason="the wrong button", clock=CLOCK)
    history = (first, second, third)
    other = renamed_target(target_of(first, "save"), "Keep changes")

    assert place(history, step, new) == Placed(Placement.PREVIOUSLY_ROLLED_BACK, 3)
    assert place(history, step, other) == Placed(Placement.PUBLISH, 3)
    assert undoing_rollback(history, step, other) is None


@given(st.lists(st.sampled_from(["open_portal", "fill_name", "save"]), max_size=4))
def test_a_heal_is_published_only_onto_its_exact_step(edits: list[str]) -> None:
    first, step, new = base()
    history = [first]
    for number, step_id in enumerate(edits):
        latest = history[-1]
        content = with_intent(latest, step_id, f"Edited {number}")
        history.append(edit_version(latest, content, summary=f"edit {number}", clock=CLOCK))

    placed = place(tuple(history), step, new)

    assert (placed.placement is Placement.PUBLISH) == (step_named(history[-1], "save") == step)
    assert placed.version == history[-1].version
