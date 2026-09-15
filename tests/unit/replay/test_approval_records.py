"""How approvals change a run's record: pausing, deciding, resuming, settling, and completing a
record a stopped process left behind."""

import pytest

from mendwork.engine.domain.approvals import (
    DecisionKind,
    ProposalOutcome,
    ProposalRecord,
    ResumeState,
    StaleReason,
    VerifiedHealRecord,
)
from mendwork.engine.domain.audit import AuditKind
from mendwork.engine.domain.heals import HealReport
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.runs import RunSegment, RunSegmentKind, RunStatus, StepStatus
from mendwork.engine.errors import MendworkError
from mendwork.engine.replay.approval_records import (
    as_left,
    decided,
    not_resumed,
    paused,
    rejected,
    resumed,
    settle,
)
from tests.unit.replay.approval_builders import (
    LATER,
    decision,
    entry,
    error,
    ledger_workflow,
    paused_run,
    paused_step,
    proposal,
    result,
)

OPENED = result("open", 0, StepStatus.SUCCEEDED, action_performed=True)
VERIFIED = (VerifiedHealRecord(step_id=StepId("open"), rung=2, identity_signature=("a", "b")),)
RESUMING = RunSegment(kind=RunSegmentKind.RESUME, started_at=LATER, proposal_id="export-1")


def test_a_run_that_paused_for_approval_records_its_proposal_as_pending() -> None:
    record = paused_run(proposals=(), resume=None)

    updated = paused(record, inputs_recoverable=False, verified=VERIFIED)

    assert updated.proposals == (ProposalRecord(proposal=proposal()),)
    assert updated.proposals[0].pending
    assert updated.resume == ResumeState(inputs_recoverable=False, verified_heals=VERIFIED)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": RunStatus.FAILED},
        {"steps": (OPENED,)},
        {"steps": (OPENED, paused_step().model_copy(update={"heal": None}))},
        {"steps": (OPENED, paused_step().model_copy(update={"heal": HealReport()}))},
    ],
)
def test_only_a_run_stopped_at_a_proposal_is_paused(overrides: dict[str, object]) -> None:
    record = paused_run(proposals=(), resume=None, **overrides)

    assert paused(record, inputs_recoverable=True, verified=()) == record


def test_a_decision_is_recorded_on_its_own_proposal_only() -> None:
    other = ProposalRecord(proposal=proposal(step_id="submit", number=2))
    record = paused_run(proposals=(ProposalRecord(proposal=proposal()), other))

    updated = decided(record, "export-1", decision())

    assert updated.proposals[0].decision == decision()
    assert updated.proposals[1] == other
    assert updated.status is RunStatus.AWAITING_APPROVAL


def test_a_rejection_ends_the_run_failed_and_keeps_the_step_as_it_paused() -> None:
    record = paused_run()

    updated = rejected(
        record, "export-1", decision(DecisionKind.REJECTED, sequence=4, reason="wrong button")
    )

    assert updated.status is RunStatus.FAILED
    assert updated.finished_at == LATER
    assert updated.error == error(
        "ProposalRejected",
        "proposal export-1 was rejected: wrong button",
        reason="proposal_rejected",
        proposal_id="export-1",
        audit_sequence=4,
    )
    assert (updated.steps[1].status, updated.steps[1].error) == (StepStatus.FAILED, updated.error)
    assert updated.steps[1].heal == record.steps[1].heal
    assert updated.paused_steps == (record.steps[1],)
    assert updated.proposals[0].decision == decision(
        DecisionKind.REJECTED, sequence=4, reason="wrong button"
    )


def test_a_rejection_without_a_reason_says_only_that_it_was_rejected() -> None:
    updated = rejected(paused_run(), "export-1", decision(DecisionKind.REJECTED))

    assert updated.error is not None
    assert updated.error.message == "proposal export-1 was rejected"


def test_an_approval_that_never_resumed_cancels_the_run() -> None:
    updated = not_resumed(decided(paused_run(), "export-1", decision()), "export-1", at=LATER)

    assert updated.status is RunStatus.CANCELLED
    assert updated.steps[1].status is StepStatus.CANCELLED
    assert updated.proposals[0].outcome is ProposalOutcome.NOT_RESUMED
    assert updated.error is not None
    assert (updated.error.type, updated.error.context["reason"]) == ("RunCancelled", "not_resumed")


def test_a_resumed_run_runs_again_from_the_approved_step_in_a_new_segment() -> None:
    record = decided(paused_run(), "export-1", decision())

    updated = resumed(record, ledger_workflow(("export", "irreversible")), proposal(), RESUMING)

    assert (updated.status, updated.finished_at, updated.error) == (RunStatus.RUNNING, None, None)
    assert [step.status for step in updated.steps] == [StepStatus.SUCCEEDED, StepStatus.NOT_RUN]
    assert updated.paused_steps == (record.steps[1],)
    assert updated.segments == (*record.segments, RESUMING)


def test_settling_records_what_came_of_the_approval() -> None:
    approved = decided(paused_run(), "export-1", decision())
    acted = result("export", 1, StepStatus.SUCCEEDED, heal=HealReport(healed_rung=2))
    gone = result(
        "export",
        1,
        StepStatus.FAILED,
        failure=error("ApprovalStale", "gone", stale_reason="different_target"),
    )

    verified = settle(approved.model_copy(update={"steps": (OPENED, acted)}), proposal())
    stale = settle(approved.model_copy(update={"steps": (OPENED, gone)}), proposal())

    assert verified.proposals[0].outcome is ProposalOutcome.ACTED_VERIFIED
    item = stale.proposals[0]
    assert (item.outcome, item.stale_reason, item.detail) == (
        ProposalOutcome.STALE,
        StaleReason.DIFFERENT_TARGET,
        "gone",
    )


def test_a_record_left_running_by_a_process_that_ended_is_finished_as_cancelled() -> None:
    running = paused_run(
        status=RunStatus.RUNNING,
        finished_at=None,
        error=None,
        proposals=(),
        resume=None,
        steps=(OPENED, result("export", 1, StepStatus.NOT_RUN)),
    )

    left = as_left(running, (), at=LATER)

    assert left.status is RunStatus.CANCELLED
    assert left.error is not None
    assert left.error.context["interruption"] == "process_ended"


def test_an_approval_only_the_audit_log_holds_is_finished_as_not_resumed() -> None:
    left = as_left(paused_run(), (entry(),), at=LATER)

    assert left.proposals[0].decision == decision()
    assert left.proposals[0].outcome is ProposalOutcome.NOT_RESUMED
    assert left.status is RunStatus.CANCELLED


def test_a_rejection_only_the_audit_log_holds_is_finished_as_a_rejection() -> None:
    left = as_left(paused_run(), (entry(AuditKind.PROPOSAL_REJECTED, reason="no"),), at=LATER)

    assert left.status is RunStatus.FAILED
    assert left.error is not None
    assert left.error.type == "ProposalRejected"
    assert left.proposals[0].decision == decision(DecisionKind.REJECTED, reason="no")


def test_an_approval_whose_resume_stopped_part_way_is_settled_as_interrupted() -> None:
    approved = decided(paused_run(), "export-1", decision())
    mid_resume = resumed(
        approved, ledger_workflow(("export", "irreversible")), proposal(), RESUMING
    )

    left = as_left(mid_resume, (entry(),), at=LATER)

    assert left.status is RunStatus.CANCELLED
    assert left.proposals[0].outcome is ProposalOutcome.INTERRUPTED


def test_entries_for_other_or_already_settled_proposals_change_nothing() -> None:
    finished = paused_run(
        status=RunStatus.SUCCEEDED,
        proposals=(
            ProposalRecord(
                proposal=proposal(), decision=decision(), outcome=ProposalOutcome.ACTED_VERIFIED
            ),
        ),
    )

    left = as_left(finished, (entry(), entry(proposal_id="other-1", sequence=2)), at=LATER)

    assert left == finished


def test_a_decision_on_a_proposal_the_run_never_made_is_an_error() -> None:
    with pytest.raises(MendworkError, match="no such proposal"):
        rejected(paused_run(), "export-9", decision(DecisionKind.REJECTED))
