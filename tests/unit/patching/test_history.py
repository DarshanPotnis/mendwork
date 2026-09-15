"""A workflow's history, step strengths, and the pending patches that still apply (ADR 0013)."""

from datetime import timedelta
from typing import Final

from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.lineage import edit_version, heal_version, roll_back_version
from mendwork.engine.domain.run_identifiers import parse_run_id
from mendwork.engine.patching.history import (
    PendingLine,
    StepStrength,
    history_entries,
    pending_lines,
    step_strengths,
    weak_summary,
)
from mendwork.engine.patching.promotion import started
from tests.fakes.clock import FakeClock
from tests.heal_changes import HEAL_RUN, heal_change
from tests.unit.patching.builders import ledger_version, with_intent
from tests.workflows import CREATED_AT_DATETIME, version

CLOCK: Final = FakeClock(CREATED_AT_DATETIME + timedelta(days=4))


def test_history_lists_every_version_newest_first_with_the_run_behind_each_heal() -> None:
    first = version()
    second = heal_version(first, heal_change(first), clock=CLOCK)
    third = roll_back_version(second, first, reason="the wrong button", clock=CLOCK)

    entries = history_entries((first, second, third))

    assert [(entry.version, entry.summary, entry.run_id) for entry in entries] == [
        (3, 'Rolled back by hand to v1: "the wrong button"', None),
        (2, "Step 3 save healed at rung 2, verified strongly", HEAL_RUN),
        (1, "First version", None),
    ]
    assert entries[0].created_at == CLOCK.now()


def test_each_step_s_strength_is_listed_and_weak_steps_are_warned_about() -> None:
    strengths = step_strengths(ledger_version())

    assert strengths == (
        StepStrength(0, "open", (), VerificationStrength.NONE),
        StepStrength(1, "export", ("text_present",), VerificationStrength.STRONG),
    )
    assert weak_summary(strengths) == (
        "1 of 2 steps is weakly verified or not verified: on those, a heal proves where the page "
        "went or what a field holds, not which element was used."
    )
    assert weak_summary(strengths[1:]) is None


def test_only_pending_patches_that_still_match_their_step_are_listed() -> None:
    workflow = ledger_version()
    change = heal_change(workflow, "export")
    first_run = parse_run_id("20260915T100000Z-00000001")
    patch = started(workflow.workflow_id, workflow.steps[1], change, first_run, CLOCK.now())
    edited = edit_version(
        workflow, with_intent(workflow, "export", "Export"), summary="e", clock=CLOCK
    )

    assert pending_lines(workflow, (patch,)) == (
        PendingLine(
            index=1,
            step_id="export",
            target='a button named "Save changes"',
            successes=1,
            runs=(first_run,),
        ),
    )
    assert pending_lines(edited, (patch,)) == ()
