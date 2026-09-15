"""What an interrupted run becomes (ADR 0011).

An interrupted run ends ``cancelled``, unless it dispatched an irreversible action: then it ends
``needs_review``, because running the workflow again could repeat that action and a person must
first check what it did. An irreversible action counts from the moment Mendwork was about to send
it (the browser may have received it), whether or not its checkpoints later passed. Neither status
is ever re-run automatically.

The same rules finish a record in three situations: after a first interrupt, from what the run
knows in memory; after a second, from the journal on disk, at once; and, for a process that ended
without either, when the record is read back.
"""

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from mendwork.engine.domain.runs import (
    ErrorCategory,
    ErrorReport,
    IrreversibleDispatch,
    Run,
    RunStatus,
    StepStatus,
)


class Interruption(StrEnum):
    """How a run was interrupted."""

    INTERRUPT = "interrupt"
    """A first Ctrl+C or SIGTERM: the run stopped where it was and recorded itself."""
    FORCED = "forced"
    """A second Ctrl+C: the process ended at once, recording only what its journal held."""
    PROCESS_ENDED = "process_ended"
    """The process ended without recording its end (killed, crashed, or the machine stopped)."""


def interrupted_status(dispatched: Sequence[IrreversibleDispatch]) -> RunStatus:
    """``needs_review`` when an irreversible action was dispatched, ``cancelled`` otherwise."""
    return RunStatus.NEEDS_REVIEW if dispatched else RunStatus.CANCELLED


def cancellation_report(
    dispatched: Sequence[IrreversibleDispatch], interruption: Interruption
) -> ErrorReport:
    """The error a run interrupted in this way records."""
    steps = list(dict.fromkeys(item.step_id for item in dispatched))
    how = {
        Interruption.INTERRUPT: "the run was interrupted",
        Interruption.FORCED: "the run was aborted by a second interrupt",
        Interruption.PROCESS_ENDED: "the process running this run ended before the run did",
    }[interruption]
    if steps:
        message = (
            f"{how} after the irreversible action of step {', '.join(steps)} was dispatched, "
            "so a person must check what it did"
        )
    else:
        message = f"{how} before it finished; no irreversible action had been dispatched"
    return ErrorReport(
        type="RunCancelled",
        message=message,
        category=ErrorCategory.STEP,
        context={
            "reason": "interrupted",
            "interruption": interruption.value,
            "irreversible_steps": steps,
        },
    )


def ended_run(
    record: Run, *, at: datetime, interruption: Interruption, step_in_progress: bool
) -> Run:
    """The final record of a run whose execution stopped, built only from its journal.

    With ``step_in_progress``, the first step the journal holds no result for is the one that was
    running, and whether its action started is not known; without it (the run stopped before its
    first step began) every step stays not run. A record that is no longer running is returned
    unchanged, so finishing a record twice changes nothing.
    """
    if record.status is not RunStatus.RUNNING:
        return record
    report = cancellation_report(record.irreversible_dispatched, interruption)
    steps = list(record.steps)
    pending = next(
        (position for position, step in enumerate(steps) if step.status is StepStatus.NOT_RUN),
        None,
    )
    if pending is not None and step_in_progress:
        steps[pending] = steps[pending].model_copy(
            update={
                "status": StepStatus.CANCELLED,
                "action_outcome_unknown": True,
                "error": report,
            }
        )
    segments = list(record.segments)
    if segments and segments[-1].finished_at is None:
        segments[-1] = segments[-1].model_copy(update={"finished_at": at})
    return record.model_copy(
        update={
            "status": interrupted_status(record.irreversible_dispatched),
            "finished_at": at,
            "steps": tuple(steps),
            "segments": tuple(segments),
            "error": report,
        }
    )
