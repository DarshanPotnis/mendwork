"""How approvals change a run's record (ADR 0011): pausing, deciding, resuming, and settling.

Every function returns a new record and changes nothing on disk, so the order of writes stays with
the callers: the audit log first, then ``run.json``.
"""

from collections.abc import Sequence
from datetime import datetime

from mendwork.engine.domain.approvals import (
    DecisionKind,
    ProposalDecision,
    ProposalOutcome,
    ProposalRecord,
    ResumeState,
    VerifiedHealRecord,
)
from mendwork.engine.domain.audit import AuditEntry, AuditKind
from mendwork.engine.domain.heals import HealProposal
from mendwork.engine.domain.runs import (
    ErrorCategory,
    ErrorReport,
    Run,
    RunSegment,
    RunStatus,
    StepStatus,
)
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import MendworkError
from mendwork.engine.replay.journal import steps_so_far
from mendwork.engine.safety.approvals import find_proposal, settled
from mendwork.engine.safety.interruption import Interruption, ended_run


def paused(
    record: Run, *, inputs_recoverable: bool, verified: tuple[VerifiedHealRecord, ...]
) -> Run:
    """A run that stopped for approval, with its proposal pending and what a resume needs.

    Any other record is returned unchanged.
    """
    stopped = record.failed_step
    heal = stopped.heal if stopped is not None else None
    if record.status is not RunStatus.AWAITING_APPROVAL or heal is None or heal.proposal is None:
        return record
    return record.model_copy(
        update={
            "proposals": (*record.proposals, ProposalRecord(proposal=heal.proposal)),
            "resume": ResumeState(inputs_recoverable=inputs_recoverable, verified_heals=verified),
        }
    )


def decided(record: Run, proposal_id: str, decision: ProposalDecision) -> Run:
    """The record with a person's decision on the proposal."""
    return _with_proposal(record, proposal_id, decision=decision)


def rejected(record: Run, proposal_id: str, decision: ProposalDecision) -> Run:
    """A rejected proposal ends its run failed; the step's paused result is kept beside it."""
    reason = f": {decision.reason}" if decision.reason else ""
    report = ErrorReport(
        type="ProposalRejected",
        message=f"proposal {proposal_id} was rejected{reason}",
        category=ErrorCategory.STEP,
        context={
            "reason": "proposal_rejected",
            "proposal_id": proposal_id,
            "audit_sequence": decision.audit_sequence,
        },
    )
    run = decided(record, proposal_id, decision)
    return _ended_at(run, _proposal(run, proposal_id), RunStatus.FAILED, report, at=decision.at)


def not_resumed(record: Run, proposal_id: str, *, at: datetime) -> Run:
    """An approval that was recorded, whose run stopped before it resumed: the run is cancelled."""
    report = ErrorReport(
        type="RunCancelled",
        message=f"proposal {proposal_id} was approved, but the run stopped before it resumed; "
        "nothing was acted on, and the workflow can be run again",
        category=ErrorCategory.STEP,
        context={"reason": "not_resumed", "proposal_id": proposal_id},
    )
    run = _with_proposal(record, proposal_id, outcome=ProposalOutcome.NOT_RESUMED)
    return _ended_at(run, _proposal(run, proposal_id), RunStatus.CANCELLED, report, at=at)


def resumed(
    record: Run, workflow: WorkflowVersion, proposal: HealProposal, segment: RunSegment
) -> Run:
    """The record as an approval resumes it: running again from the approved step."""
    index = proposal.step_index
    return record.model_copy(
        update={
            "status": RunStatus.RUNNING,
            "finished_at": None,
            "error": None,
            "steps": steps_so_far(workflow, record.steps[:index]),
            "paused_steps": (*record.paused_steps, record.steps[index]),
            "segments": (*record.segments, segment),
        }
    )


def settle(record: Run, proposal: HealProposal) -> Run:
    """The approved proposal's outcome, from what its step did once the run resumed."""
    result = settled(record.steps[proposal.step_index])
    return _with_proposal(
        record,
        proposal.id,
        outcome=result.outcome,
        stale_reason=result.stale_reason,
        detail=result.detail,
    )


def as_left(record: Run, entries: Sequence[AuditEntry], *, at: datetime) -> Run:
    """A record no process holds, completed from its journal and the audit log.

    A process can stop between any two writes: while a step runs (the record still says running),
    between the audit entry and the record (the decision is only in the log), or between an
    approval and its resume. Only a record that no process has claimed may be completed this way.
    """
    run = ended_run(record, at=at, interruption=Interruption.PROCESS_ENDED, step_in_progress=True)
    for entry in entries:
        found = find_proposal(run, entry.proposal_id)
        if found is None or found.decision is not None:
            continue
        decision = ProposalDecision(
            kind=(
                DecisionKind.APPROVED
                if entry.kind is AuditKind.PROPOSAL_APPROVED
                else DecisionKind.REJECTED
            ),
            at=entry.at,
            audit_sequence=entry.sequence,
            reason=entry.reason,
        )
        if decision.kind is DecisionKind.REJECTED:
            run = rejected(run, entry.proposal_id, decision)
        else:
            run = decided(run, entry.proposal_id, decision)
    for item in run.proposals:
        approved = item.decision is not None and item.decision.kind is DecisionKind.APPROVED
        if not approved or item.outcome is not None:
            continue
        if run.status is RunStatus.AWAITING_APPROVAL:
            run = not_resumed(run, item.proposal.id, at=at)
        else:
            run = settle(run, item.proposal)
    return run


def _with_proposal(record: Run, proposal_id: str, **changes: object) -> Run:
    updated = tuple(
        item.model_copy(update=changes) if item.proposal.id == proposal_id else item
        for item in record.proposals
    )
    return record.model_copy(update={"proposals": updated})


def _proposal(record: Run, proposal_id: str) -> HealProposal:
    found = find_proposal(record, proposal_id)
    if found is None:
        raise MendworkError("the run has no such proposal", proposal_id=proposal_id)
    return found.proposal


def _ended_at(
    record: Run, proposal: HealProposal, status: RunStatus, report: ErrorReport, *, at: datetime
) -> Run:
    index = proposal.step_index
    steps = list(record.steps)
    paused_step = steps[index]
    step_status = StepStatus.CANCELLED if status is RunStatus.CANCELLED else StepStatus.FAILED
    steps[index] = paused_step.model_copy(update={"status": step_status, "error": report})
    return record.model_copy(
        update={
            "status": status,
            "finished_at": at,
            "error": report,
            "steps": tuple(steps),
            "paused_steps": (*record.paused_steps, paused_step),
        }
    )
