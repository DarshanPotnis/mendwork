"""A verified heal's change record, built from the run that verified it (ADR 0013).

The record keeps what a person reviewing the version history needs and nothing a run record does not
already hold: the target before and after, the rung, the numbers the deciding rung accepted it on or
what asking a model cost, the checkpoints that passed and how much they prove, the approval an
irreversible step needed, and where the run's evidence is.
"""

from mendwork.engine.domain.changes import (
    MODEL_RUNG,
    HealChange,
    HealEvidence,
    ModelHealUsage,
    Promotion,
)
from mendwork.engine.domain.enums import ChangeKind
from mendwork.engine.domain.heals import HealAttemptReport, Verification
from mendwork.engine.domain.model_evidence import ModelChoiceEvidence, ModelUsageTotals
from mendwork.engine.domain.runs import Run
from mendwork.engine.domain.steps import step_target
from mendwork.engine.errors import MendworkError
from mendwork.engine.patching.eligibility import VerifiedHeal
from mendwork.engine.replay.artifact_names import REPORT
from mendwork.engine.safety.heal_policy import verification_strength


def heal_change(heal: VerifiedHeal, run: Run, promotion: Promotion) -> HealChange:
    """The change record for a verified heal. Raises ValidationError if it is not a real change."""
    old = step_target(heal.step)
    if old is None:
        raise MendworkError("only a step with a target can be healed", step_id=heal.step.id)
    report = deciding_report(heal)
    passed = sorted(
        (item for item in heal.result.checkpoints if item.passed), key=lambda c: c.index
    )
    return HealChange(
        kind=ChangeKind.HEAL,
        step_id=heal.step.id,
        rung=heal.rung,
        old_target=old,
        new_target=heal.found,
        checkpoints=tuple(item.kind for item in passed),
        strength=verification_strength(heal.step.checkpoints),
        score=report.score if report is not None else None,
        margin=report.margin if report is not None else None,
        threshold=report.threshold if report is not None else None,
        required_margin=report.required_margin if report is not None else None,
        model=_model_usage(heal, run, report) if heal.rung == MODEL_RUNG else None,
        approval=heal.approval,
        evidence=HealEvidence(
            run_id=run.run_id,
            report=REPORT,
            step_screenshot=heal.result.artifacts.screenshot,
            found_screenshot=heal.result.found.screenshot if heal.result.found else None,
        ),
        promotion=promotion,
    )


def deciding_report(heal: VerifiedHeal) -> HealAttemptReport | None:
    """The rung report whose heal passed verification."""
    attempts = heal.result.heal.attempts if heal.result.heal is not None else ()
    passed = [report for report in attempts if report.verification is Verification.PASSED]
    return passed[-1] if passed else None


def model_heal_usage(evidence: ModelChoiceEvidence | None) -> ModelHealUsage | None:
    """What a Rung 3 decision's calls cost, or None when no call was made."""
    if evidence is None or not evidence.calls:
        return None
    totals = ModelUsageTotals()
    for call in evidence.calls:
        totals = totals.plus(call.usage)
    first = evidence.calls[0].usage
    return ModelHealUsage(
        provider=first.provider,
        model=first.model,
        prompt_version=evidence.prompt_version,
        calls=totals.calls,
        input_tokens=totals.input_tokens,
        output_tokens=totals.output_tokens,
        estimated_cost_usd=totals.estimated_cost_usd,
        unpriced_calls=totals.unpriced_calls,
        unreported_token_calls=totals.unreported_token_calls,
        confidence=evidence.confidence,
    )


def _model_usage(
    heal: VerifiedHeal, run: Run, report: HealAttemptReport | None
) -> ModelHealUsage | None:
    usage = model_heal_usage(report.model if report is not None else None)
    if usage is not None:
        return usage
    # A resumed run finds an approved model pick again without asking; the proposal kept the calls.
    proposed = next(
        (
            item.proposal.model
            for item in run.proposals
            if item.proposal.step_id == heal.step.id and item.proposal.model is not None
        ),
        None,
    )
    return model_heal_usage(proposed)
