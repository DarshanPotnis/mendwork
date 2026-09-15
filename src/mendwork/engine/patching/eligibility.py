"""Which heals of a finished run may become versions, and what the run says about pending patches.

A heal qualifies when the run succeeded, so every step after it passed as well (further evidence for
an earlier heal, most of all on a weakly verified step); its step succeeded on the healed element;
the element was fingerprinted; and, for an irreversible step, a person's approval let it act and the
action was verified. Every heal that does not qualify is reported with its reason, so none
disappears silently (ADR 0013).
"""

from dataclasses import dataclass

from mendwork.engine.domain.approvals import DecisionKind, ProposalOutcome
from mendwork.engine.domain.changes import ApprovalReference
from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import HealedRung
from mendwork.engine.domain.patches import PatchOutcome, PatchResult
from mendwork.engine.domain.runs import Run, RunStatus, StepResult, StepStatus
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.safety.heal_policy import verification_strength


@dataclass(frozen=True, slots=True)
class VerifiedHeal:
    """A heal a succeeded run verified, with what its change record needs."""

    index: int
    step: Step
    result: StepResult
    rung: HealedRung
    found: Fingerprint
    approval: ApprovalReference | None


@dataclass(frozen=True, slots=True)
class FirstTrySuccess:
    """A pending patch whose target a succeeded run found at Rung 0 and verified."""

    pending_id: str
    step: Step


@dataclass(frozen=True, slots=True)
class RunPatchFacts:
    """What a finished run means for patching."""

    heals: tuple[VerifiedHeal, ...] = ()
    refused: tuple[PatchOutcome, ...] = ()
    """Verified heals that cannot become versions, each with its reason."""
    first_tries: tuple[FirstTrySuccess, ...] = ()
    recorded: tuple[Step, ...] = ()
    """Steps a succeeded run resolved on their recorded target, so the page went back for them."""


def run_patch_facts(run: Run, workflow: WorkflowVersion) -> RunPatchFacts:
    """The heals a finished run may turn into versions, and what else it proved."""
    pairs = list(zip(workflow.steps, run.steps, strict=False))
    healed = [
        (index, step, result) for index, (step, result) in enumerate(pairs) if _healed(result)
    ]
    source = run.source
    if source is None or not source.saves_heals:
        detail = (
            "the run has no workflow source"
            if source is None or source.not_saved is None
            else f"this run does not save heals ({source.not_saved.value})"
        )
        return RunPatchFacts(
            refused=tuple(
                _refused(step, result, PatchResult.NOT_SAVED, detail) for _, step, result in healed
            )
        )
    if run.status is not RunStatus.SUCCEEDED:
        detail = f"the run ended {run.status.value}, so none of its heals is saved"
        return RunPatchFacts(
            refused=tuple(
                _refused(step, result, PatchResult.RUN_NOT_SUCCEEDED, detail)
                for _, step, result in healed
            )
        )
    heals: list[VerifiedHeal] = []
    refused: list[PatchOutcome] = []
    for index, step, result in healed:
        verified = _verified(run, index, step, result)
        if isinstance(verified, PatchOutcome):
            refused.append(verified)
        else:
            heals.append(verified)
    return RunPatchFacts(
        heals=tuple(heals),
        refused=tuple(refused),
        first_tries=tuple(
            FirstTrySuccess(result.target.pending_patch, step)
            for step, result in pairs
            if result.status is StepStatus.SUCCEEDED
            and result.target is not None
            and result.target.pending_patch is not None
        ),
        recorded=tuple(step for step, result in pairs if _on_recorded_target(step, result)),
    )


def approval_for(run: Run, step_id: str) -> ApprovalReference | None:
    """The approval that let the step act on its heal, when the action was verified."""
    for item in reversed(run.proposals):
        decision = item.decision
        if (
            item.proposal.step_id == step_id
            and decision is not None
            and decision.kind is DecisionKind.APPROVED
            and item.outcome is ProposalOutcome.ACTED_VERIFIED
        ):
            return ApprovalReference(
                proposal_id=item.proposal.id,
                audit_sequence=decision.audit_sequence,
                decided_at=decision.at,
            )
    return None


def _healed(result: StepResult) -> bool:
    heal = result.heal
    return (
        result.status is StepStatus.SUCCEEDED and heal is not None and heal.healed_rung is not None
    )


def _verified(run: Run, index: int, step: Step, result: StepResult) -> VerifiedHeal | PatchOutcome:
    heal = result.heal
    rung = heal.healed_rung if heal is not None else None
    found = result.found
    if rung is None or found is None or found.fingerprint is None:
        problem = found.problem.value if found is not None and found.problem is not None else None
        detail = f"the healed element could not be fingerprinted ({problem or 'not captured'})"
        return _refused(step, result, PatchResult.NOT_CAPTURABLE, detail)
    approval = approval_for(run, step.id)
    if step.risk is RiskLevel.IRREVERSIBLE and approval is None:
        return _refused(
            step,
            result,
            PatchResult.NOT_APPROVED,
            "an irreversible step's heal becomes a version only after an approval that acted and "
            "was verified",
        )
    return VerifiedHeal(
        index=index, step=step, result=result, rung=rung, found=found.fingerprint, approval=approval
    )


def _on_recorded_target(step: Step, result: StepResult) -> bool:
    target = result.target
    return (
        result.status is StepStatus.SUCCEEDED
        and step_target(step) is not None
        and result.heal is None
        and target is not None
        and target.resolved_rank is not None
        and target.healed_rung is None
        and target.pending_patch is None
    )


def _refused(step: Step, result: StepResult, outcome: PatchResult, detail: str) -> PatchOutcome:
    heal = result.heal
    return PatchOutcome(
        step_id=step.id,
        result=outcome,
        rung=heal.healed_rung if heal is not None else None,
        strength=verification_strength(step.checkpoints),
        detail=detail,
    )
