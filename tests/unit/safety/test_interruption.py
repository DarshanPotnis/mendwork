"""What an interrupted run becomes: cancelled, or needing review after an irreversible dispatch."""

from datetime import UTC, datetime
from typing import Final

import pytest

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.runs import (
    ErrorCategory,
    IrreversibleDispatch,
    Run,
    RunSegment,
    RunSegmentKind,
    RunStatus,
    StepResult,
    StepStatus,
    parse_run_id,
)
from mendwork.engine.safety.interruption import (
    Interruption,
    cancellation_report,
    ended_run,
    interrupted_status,
)

STARTED: Final = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
ENDED: Final = datetime(2026, 9, 14, 10, 5, tzinfo=UTC)
SENT: Final = IrreversibleDispatch(step_id="submit_order", index=1, at=STARTED, segment=1)


def record(*statuses: StepStatus, dispatched: tuple[IrreversibleDispatch, ...] = ()) -> Run:
    return Run(
        run_id=parse_run_id("20260914T100000Z-00000001"),
        workflow_id="order_flow",
        workflow_version=1,
        status=RunStatus.RUNNING,
        started_at=STARTED,
        steps=tuple(
            StepResult(step_id=f"step_{index}", index=index, action=ActionType.CLICK, status=status)
            for index, status in enumerate(statuses)
        ),
        segments=(RunSegment(kind=RunSegmentKind.RUN, started_at=STARTED),),
        irreversible_dispatched=dispatched,
    )


def test_a_run_that_dispatched_nothing_irreversible_is_cancelled_otherwise_it_needs_review() -> (
    None
):
    assert interrupted_status(()) is RunStatus.CANCELLED
    assert interrupted_status((SENT,)) is RunStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("interruption", "opening"),
    [
        (Interruption.INTERRUPT, "the run was interrupted"),
        (Interruption.FORCED, "the run was aborted by a second interrupt"),
        (Interruption.PROCESS_ENDED, "the process running this run ended before the run did"),
    ],
)
def test_the_report_says_how_the_run_was_interrupted_and_what_went_out(
    interruption: Interruption, opening: str
) -> None:
    quiet = cancellation_report((), interruption)
    sent = cancellation_report((SENT, SENT), interruption)

    assert (
        quiet.message == f"{opening} before it finished; no irreversible action had been dispatched"
    )
    assert sent.message == (
        f"{opening} after the irreversible action of step submit_order was dispatched, so a "
        "person must check what it did"
    )
    assert (sent.type, sent.category) == ("RunCancelled", ErrorCategory.STEP)
    assert sent.context == {
        "reason": "interrupted",
        "interruption": interruption.value,
        "irreversible_steps": ["submit_order"],
    }


def test_the_step_in_progress_is_cancelled_with_an_unknown_outcome_and_later_steps_stay_unrun() -> (
    None
):
    running = record(StepStatus.SUCCEEDED, StepStatus.NOT_RUN, StepStatus.NOT_RUN)

    ended = ended_run(running, at=ENDED, interruption=Interruption.FORCED, step_in_progress=True)

    assert ended.status is RunStatus.CANCELLED
    assert [step.status for step in ended.steps] == [
        StepStatus.SUCCEEDED,
        StepStatus.CANCELLED,
        StepStatus.NOT_RUN,
    ]
    assert (ended.steps[1].action_outcome_unknown, ended.steps[2].action_outcome_unknown) == (
        True,
        False,
    )
    assert ended.steps[1].error == ended.error
    assert (ended.finished_at, ended.segments[0].finished_at) == (ENDED, ENDED)


def test_a_run_stopped_before_its_first_step_leaves_every_step_unrun() -> None:
    running = record(StepStatus.NOT_RUN, StepStatus.NOT_RUN)

    ended = ended_run(
        running, at=ENDED, interruption=Interruption.INTERRUPT, step_in_progress=False
    )

    assert ended.status is RunStatus.CANCELLED
    assert {step.status for step in ended.steps} == {StepStatus.NOT_RUN}


def test_a_run_that_dispatched_an_irreversible_action_ends_needing_review() -> None:
    running = record(
        StepStatus.SUCCEEDED, StepStatus.SUCCEEDED, StepStatus.NOT_RUN, dispatched=(SENT,)
    )

    ended = ended_run(
        running, at=ENDED, interruption=Interruption.PROCESS_ENDED, step_in_progress=True
    )

    assert ended.status is RunStatus.NEEDS_REVIEW
    assert ended.error is not None
    assert ended.error.context["irreversible_steps"] == ["submit_order"]


def test_a_record_that_already_ended_is_left_as_it_is() -> None:
    finished = record(StepStatus.SUCCEEDED).model_copy(update={"status": RunStatus.SUCCEEDED})

    assert (
        ended_run(finished, at=ENDED, interruption=Interruption.FORCED, step_in_progress=True)
        is finished
    )


def test_a_journal_holding_every_result_ends_without_inventing_a_step() -> None:
    running = record(StepStatus.SUCCEEDED, StepStatus.SUCCEEDED)

    ended = ended_run(running, at=ENDED, interruption=Interruption.FORCED, step_in_progress=True)

    assert [step.status for step in ended.steps] == [StepStatus.SUCCEEDED, StepStatus.SUCCEEDED]
    assert ended.status is RunStatus.CANCELLED
