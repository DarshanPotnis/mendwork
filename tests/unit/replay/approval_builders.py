"""Records for approval tests: a ledger run paused at its irreversible export, and its decisions."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from mendwork.engine.domain.approvals import (
    DecisionKind,
    ProposalDecision,
    ProposalRecord,
    ResumeState,
)
from mendwork.engine.domain.audit import GENESIS_SHA256, AuditDraft, AuditEntry, AuditKind, chained
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.heals import (
    CandidateOrigin,
    FeatureScores,
    HealedRung,
    HealProposal,
    HealReport,
    ScoredCandidate,
)
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.runs import (
    ErrorCategory,
    ErrorReport,
    Run,
    RunSegment,
    RunSegmentKind,
    RunStatus,
    StepResult,
    StepStatus,
    parse_run_id,
)
from mendwork.engine.domain.targets import IdentityReport, TargetEvidence
from mendwork.engine.domain.workflow import WorkflowVersion
from tests.unit.healing.builders import export_button
from tests.workflows import version

RUN_ID: Final = parse_run_id("20260914T101500Z-0000abcd")
AT: Final = datetime(2026, 9, 14, 10, 15, tzinfo=UTC)
LATER: Final = datetime(2026, 9, 14, 11, 0, tzinfo=UTC)
DIGEST: Final = "a" * 64
IDENTITY: Final = IdentityReport(tag="button", role="button", name="Export ledger", confirmed=True)
SIGNATURE: Final = (
    "button",
    "button",
    "export ledger",
    "button",
    "export-ledger",
    "",
    "ledger-export",
    "",
    "main > article > div > button",
    "Quarterly ledger",
)
FEATURES: Final = FeatureScores(
    name=1, label=1, attributes=1, role=1, tag_type=1, nearby_text=1, structural_path=1, position=1
)


def proposal(
    *,
    step_id: str = "export",
    index: int = 1,
    number: int = 1,
    rung: HealedRung = 2,
    identity: IdentityReport = IDENTITY,
    signature: tuple[str, ...] = SIGNATURE,
) -> HealProposal:
    return HealProposal(
        id=f"{step_id}-{number}",
        step_id=StepId(step_id),
        step_index=index,
        rung=rung,
        candidate=ScoredCandidate(
            id="c1", origin=CandidateOrigin.PAGE, identity=identity, score=0.9, features=FEATURES
        ),
        identity_signature=signature,
        margin=0.4,
        threshold=0.6,
        required_margin=0.15,
        reason="the step is irreversible",
    )


def error(error_type: str, message: str = "m", **context: JsonValue) -> ErrorReport:
    return ErrorReport(
        type=error_type, message=message, category=ErrorCategory.STEP, context=context
    )


def result(
    step_id: str,
    index: int,
    status: StepStatus,
    *,
    failure: ErrorReport | None = None,
    heal: HealReport | None = None,
    action_performed: bool = False,
    target: TargetEvidence | None = None,
) -> StepResult:
    return StepResult(
        step_id=StepId(step_id),
        index=index,
        action=ActionType.NAVIGATE if index == 0 else ActionType.CLICK,
        status=status,
        error=failure,
        heal=heal,
        action_performed=action_performed,
        target=target,
    )


def paused_step(heal_proposal: HealProposal | None = None) -> StepResult:
    return result(
        "export",
        1,
        StepStatus.AWAITING_APPROVAL,
        failure=error("ApprovalRequired", proposal_id="export-1"),
        heal=HealReport(proposal=heal_proposal or proposal()),
    )


def paused_run(**overrides: object) -> Run:
    values: Mapping[str, object] = {
        "run_id": RUN_ID,
        "workflow_id": "ledger",
        "workflow_version": 1,
        "workflow_sha256": DIGEST,
        "status": RunStatus.AWAITING_APPROVAL,
        "started_at": AT,
        "finished_at": AT,
        "duration_ms": 900,
        "steps": (
            result("open", 0, StepStatus.SUCCEEDED, action_performed=True),
            paused_step(),
        ),
        "error": error("ApprovalRequired", proposal_id="export-1"),
        "segments": (
            RunSegment(kind=RunSegmentKind.RUN, started_at=AT, finished_at=AT, duration_ms=900),
        ),
        "proposals": (ProposalRecord(proposal=proposal()),),
        "resume": ResumeState(inputs_recoverable=True),
    }
    return Run.model_validate({**values, **overrides})


def decision(
    kind: DecisionKind = DecisionKind.APPROVED, *, sequence: int = 1, reason: str | None = None
) -> ProposalDecision:
    return ProposalDecision(kind=kind, at=LATER, audit_sequence=sequence, reason=reason)


def entry(
    kind: AuditKind = AuditKind.PROPOSAL_APPROVED,
    *,
    sequence: int = 1,
    previous: str = GENESIS_SHA256,
    proposal_id: str = "export-1",
    reason: str | None = None,
) -> AuditEntry:
    draft = AuditDraft(
        kind=kind,
        at=LATER,
        run_id=RUN_ID,
        workflow_id="ledger",
        workflow_version=1,
        step_id=StepId("export"),
        step_index=1,
        proposal_id=proposal_id,
        reason=reason,
    )
    return chained(draft, sequence=sequence, previous_sha256=previous)


def click(step_id: str, risk: str) -> dict[str, object]:
    return {
        "id": step_id,
        "intent": f"Click {step_id}",
        "action": "click",
        "risk": risk,
        "target": export_button().model_dump(mode="json"),
        "checkpoints": [{"kind": "text_present", "text": "Done"}],
    }


def ledger_workflow(*clicks: tuple[str, str]) -> WorkflowVersion:
    """Open the ledger, then each (step id, risk) click in order."""
    opening = {
        "id": "open",
        "intent": "Open the ledger",
        "action": "navigate",
        "risk": "safe",
        "value": {"kind": "literal", "value": "https://ledger.example.test/ledger"},
    }
    return version(steps=[opening, *(click(step_id, risk) for step_id, risk in clicks)])
