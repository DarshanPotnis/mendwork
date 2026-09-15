"""Approval rules (ADR 0011): when a decision may be recorded, when a run may resume, and whether
the resumed page still shows the element a person approved.

An approval names one element, matched on what it is (tag, role, name, type, id, name attribute,
test id, href, structural path, and nearby text), never on where it sits. If the page no longer
shows that element, nothing acts and the run fails: a changed page gets a fresh run and a fresh
proposal, never an old approval stretched over a new element.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from mendwork.engine.domain.approvals import ProposalOutcome, ProposalRecord, StaleReason
from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.heals import AbstentionReason, HealProposal
from mendwork.engine.domain.runs import Run, RunStatus, StepResult, StepStatus
from mendwork.engine.domain.targets import IdentityReport
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import ApprovalStale

_MODEL_RUNG: Final = 3
_STALE_FOR_ABSTENTION: Final = {
    AbstentionReason.PAGE_NEVER_STABLE: StaleReason.PAGE_NEVER_STABLE,
    AbstentionReason.BELOW_THRESHOLD: StaleReason.NO_LONGER_ACCEPTED,
    AbstentionReason.BELOW_MARGIN: StaleReason.NO_LONGER_ACCEPTED,
    AbstentionReason.TOP_REJECTED: StaleReason.REFUSED,
    AbstentionReason.MODEL_CHOICE_REFUSED: StaleReason.REFUSED,
}
_STALE_WORDS: Final = {
    StaleReason.PAGE_NOT_REESTABLISHED: "an earlier step could not be replayed to rebuild its page",
    StaleReason.APPROVED_TARGET_NOT_FOUND: "the approved element is not on the page",
    StaleReason.DIFFERENT_TARGET: "the heal ladder now finds a different element",
    StaleReason.IDENTITY_CHANGED: "the element's confirmed role or name changed",
    StaleReason.REFUSED: "a safety rule now refuses the element",
    StaleReason.NO_LONGER_ACCEPTED: "the element no longer clears the score threshold or margin",
    StaleReason.PAGE_NEVER_STABLE: "the page never stopped changing",
}


class NotPending(StrEnum):
    """Why a proposal cannot take a decision."""

    UNKNOWN_PROPOSAL = "unknown_proposal"
    ALREADY_DECIDED = "already_decided"
    RUN_NOT_AWAITING_APPROVAL = "run_not_awaiting_approval"


class NotResumable(StrEnum):
    """Why an approved run could not resume."""

    RECORD_VERSION = "record_version"
    INPUTS_NOT_RECOVERABLE = "inputs_not_recoverable"
    EARLIER_STEP_NOT_SUCCEEDED = "earlier_step_not_succeeded"
    WORKFLOW_UNREADABLE = "workflow_unreadable"
    WORKFLOW_CHANGED = "workflow_changed"
    EARLIER_IRREVERSIBLE_STEP = "earlier_irreversible_step"


@dataclass(frozen=True, slots=True)
class Problem:
    """A rule that stops a decision or a resume, with words for the person who asked."""

    reason: str
    message: str


def find_proposal(run: Run, proposal_id: str) -> ProposalRecord | None:
    """The run's proposal with this id."""
    return next((item for item in run.proposals if item.proposal.id == proposal_id), None)


def decision_problem(run: Run, proposal_id: str) -> Problem | None:
    """Why the proposal cannot be approved or rejected now, or None when it can."""
    record = find_proposal(run, proposal_id)
    if record is None:
        known = ", ".join(item.proposal.id for item in run.proposals) or "none"
        return Problem(
            NotPending.UNKNOWN_PROPOSAL,
            f"run {run.run_id} has no proposal {proposal_id} (its proposals: {known})",
        )
    if record.decision is not None:
        return Problem(
            NotPending.ALREADY_DECIDED,
            f"proposal {proposal_id} was already {record.decision.kind.value} "
            f"(audit entry {record.decision.audit_sequence})",
        )
    if run.status is not RunStatus.AWAITING_APPROVAL:
        return Problem(
            NotPending.RUN_NOT_AWAITING_APPROVAL,
            f"run {run.run_id} is {run.status.value}, not awaiting approval",
        )
    return None


def record_problem(run: Run, proposal: HealProposal) -> Problem | None:
    """Why the run's record does not allow a resume, or None when it does."""
    if run.record_version < 2:
        return Problem(
            NotResumable.RECORD_VERSION,
            f"run {run.run_id} was recorded before runs could be resumed; run the workflow again",
        )
    if run.resume is None or not run.resume.inputs_recoverable:
        return Problem(
            NotResumable.INPUTS_NOT_RECOVERABLE,
            "an input of this run held a secret's value, and its record keeps only the redacted "
            "form, so the run cannot be resumed with the original; run the workflow again",
        )
    earlier = run.steps[: proposal.step_index]
    for step in earlier:
        if step.status is not StepStatus.SUCCEEDED:
            return Problem(
                NotResumable.EARLIER_STEP_NOT_SUCCEEDED,
                f"step {step.index + 1} {step.step_id} did not succeed, so the page the approved "
                "step paused on cannot be rebuilt",
            )
    return None


def snapshot_problem(
    run: Run, workflow: WorkflowVersion, digest: str, proposal: HealProposal
) -> Problem | None:
    """Why the saved workflow does not allow a resume, or None when it does.

    A resume replays every step before the approved one in a new browser, so an irreversible step
    among them would run its action a second time.
    """
    if run.workflow_sha256 != digest:
        return Problem(
            NotResumable.WORKFLOW_CHANGED,
            f"the workflow saved with run {run.run_id} changed after the run paused",
        )
    for index, step in enumerate(workflow.steps[: proposal.step_index]):
        if step.risk is RiskLevel.IRREVERSIBLE:
            return Problem(
                NotResumable.EARLIER_IRREVERSIBLE_STEP,
                f"resuming replays every step before the approved one, and step {index + 1} "
                f"{step.id} is irreversible, so its action would run again; run the workflow "
                "again instead",
            )
    return None


def stale_reason_for(proposal: HealProposal, abstention: AbstentionReason | None) -> StaleReason:
    """Why the approved element no longer matched, when the ladder accepted nothing.

    A model's pick is found again only among Rung 3's eligible candidates, so for a Rung 3
    proposal Rung 2's own threshold or margin says nothing about the element.
    """
    if abstention is None:
        return StaleReason.APPROVED_TARGET_NOT_FOUND
    reason = _STALE_FOR_ABSTENTION.get(abstention, StaleReason.APPROVED_TARGET_NOT_FOUND)
    if proposal.rung == _MODEL_RUNG and reason is StaleReason.NO_LONGER_ACCEPTED:
        return StaleReason.APPROVED_TARGET_NOT_FOUND
    return reason


def approval_mismatch(
    proposal: HealProposal, *, identity: tuple[str, ...], confirmed: IdentityReport
) -> StaleReason | None:
    """Whether an accepted element is the approved one: None when it is, or why not.

    ``identity`` is the element's identity signature scrubbed of secrets, as the proposal records
    its own, and ``confirmed`` its identity as Playwright confirmed it, scrubbed.
    """
    if identity != proposal.identity_signature:
        return StaleReason.DIFFERENT_TARGET
    if _identity_key(confirmed) != _identity_key(proposal.candidate.identity):
        return StaleReason.IDENTITY_CHANGED
    return None


def approval_stale(
    proposal: HealProposal, reason: StaleReason, *, detail: str | None = None
) -> ApprovalStale:
    """The error an approved step fails with when the page no longer shows the approved element."""
    extra = {"detail": detail} if detail is not None else {}
    return ApprovalStale(
        f"the page no longer matches proposal {proposal.id}: {_STALE_WORDS[reason]}; nothing was "
        "acted on",
        reason="approval_stale",
        stale_reason=reason.value,
        proposal_id=proposal.id,
        **extra,
    )


@dataclass(frozen=True, slots=True)
class Settled:
    """What came of an approval."""

    outcome: ProposalOutcome
    stale_reason: StaleReason | None = None
    detail: str | None = None


def settled(step: StepResult) -> Settled:
    """What came of an approval, from the approved step's result once the run resumed."""
    healed = step.heal is not None and step.heal.healed_rung is not None
    match step.status:
        case StepStatus.SUCCEEDED:
            return Settled(ProposalOutcome.ACTED_VERIFIED if healed else ProposalOutcome.NOT_NEEDED)
        case StepStatus.NEEDS_REVIEW:
            return Settled(ProposalOutcome.ACTED_UNVERIFIED)
        case StepStatus.CANCELLED | StepStatus.NOT_RUN:
            return Settled(ProposalOutcome.INTERRUPTED)
        case _:
            return _unsuccessful(step)


def _unsuccessful(step: StepResult) -> Settled:
    error = step.error
    detail = f"{error.type}: {error.message}" if error is not None else None
    if error is not None and error.type == ApprovalStale.__name__:
        return Settled(
            ProposalOutcome.STALE,
            StaleReason(str(error.context.get("stale_reason"))),
            error.message,
        )
    if step.action_performed:
        healed = step.target is not None and step.target.healed_rung is not None
        outcome = ProposalOutcome.ACTED_UNVERIFIED if healed else ProposalOutcome.NOT_NEEDED
        return Settled(outcome, detail=detail)
    never_stable = error is not None and error.type == "PageNeverStable"
    reason = (
        StaleReason.PAGE_NEVER_STABLE if never_stable else StaleReason.APPROVED_TARGET_NOT_FOUND
    )
    return Settled(ProposalOutcome.STALE, reason, detail)


def _identity_key(report: IdentityReport) -> tuple[str, str | None, str | None, str, bool | None]:
    return (report.tag, report.input_type, report.role, report.name, report.confirmed)
