"""A verified heal's change record, built from the run that verified it (ADR 0013)."""

from dataclasses import replace
from decimal import Decimal
from typing import Final

import pytest

from mendwork.engine.domain.approvals import ProposalRecord
from mendwork.engine.domain.changes import HealEvidence, ModelHealUsage, Promotion
from mendwork.engine.domain.enums import CheckpointKind, PromotionPolicy, VerificationStrength
from mendwork.engine.domain.model_evidence import (
    ModelCall,
    ModelCallOutcome,
    ModelCallPurpose,
    ModelChoiceEvidence,
    ModelUsage,
)
from mendwork.engine.domain.runs import ArtifactName, Run
from mendwork.engine.errors import MendworkError
from mendwork.engine.patching.eligibility import VerifiedHeal, run_patch_facts
from mendwork.engine.patching.heal_changes import deciding_report, heal_change
from tests.unit.patching.builders import EXPORT, ledger, ledger_page, ledger_version
from tests.unit.replay.approval_builders import proposal

pytestmark = pytest.mark.asyncio


def usage(cost: Decimal | None, input_tokens: int, output_tokens: int) -> ModelUsage:
    return ModelUsage(
        provider="ollama",
        model="qwen3:4b-instruct-2507-q4_K_M",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=3_000,
        http_attempts=1,
        estimated_cost_usd=cost,
    )


EVIDENCE: Final = ModelChoiceEvidence(
    prompt_version="choose-candidate/1",
    calls=(
        ModelCall(
            purpose=ModelCallPurpose.CHOOSE,
            outcome=ModelCallOutcome.INVALID_OUTPUT,
            usage=usage(Decimal("0.0001"), 400, 50),
        ),
        ModelCall(
            purpose=ModelCallPurpose.REPAIR,
            outcome=ModelCallOutcome.ANSWERED,
            usage=usage(None, 420, 60),
        ),
    ),
    choice=1,
    confidence=0.8,
    reason="the same control, renamed",
)


async def verified() -> tuple[Run, VerifiedHeal]:
    made = await ledger()
    workflow = ledger_version()
    run = await made.run(ledger_page(), workflow)
    [heal] = run_patch_facts(run, workflow).heals
    return run, heal


def promotion(run: Run) -> Promotion:
    return Promotion(policy=PromotionPolicy.IMMEDIATE, runs=(run.run_id,))


def as_model_heal(heal: VerifiedHeal, evidence: ModelChoiceEvidence | None) -> VerifiedHeal:
    report = heal.result.heal
    assert report is not None
    last = report.attempts[-1].model_copy(update={"rung": 3, "model": evidence})
    healed = report.model_copy(update={"attempts": (*report.attempts[:-1], last), "healed_rung": 3})
    return replace(heal, rung=3, result=heal.result.model_copy(update={"heal": healed}))


async def test_a_rung_2_heal_keeps_its_targets_numbers_checkpoints_and_evidence() -> None:
    run, heal = await verified()

    change = heal_change(heal, run, promotion(run))

    assert (change.step_id, change.rung, change.old_target, change.new_target) == (
        "export",
        2,
        EXPORT,
        heal.found,
    )
    assert (change.checkpoints, change.strength) == (
        (CheckpointKind.TEXT_PRESENT,),
        VerificationStrength.STRONG,
    )
    report = deciding_report(heal)
    assert report is not None
    assert report.rung == 2
    assert (change.score, change.margin, change.threshold, change.required_margin) == (
        report.score,
        report.margin,
        0.6,
        0.15,
    )
    assert change.evidence == HealEvidence(
        run_id=run.run_id,
        report=ArtifactName("report.html"),
        step_screenshot=ArtifactName("steps/002_export.png"),
        found_screenshot=ArtifactName("steps/002_export.found.png"),
    )
    assert (change.model, change.approval, change.promotion) == (None, None, promotion(run))


async def test_a_model_heal_records_what_every_call_cost_and_how_sure_the_model_was() -> None:
    run, heal = await verified()

    change = heal_change(as_model_heal(heal, EVIDENCE), run, promotion(run))

    assert change.model == ModelHealUsage(
        provider="ollama",
        model="qwen3:4b-instruct-2507-q4_K_M",
        prompt_version="choose-candidate/1",
        calls=2,
        input_tokens=820,
        output_tokens=110,
        estimated_cost_usd=Decimal("0.0001"),
        unpriced_calls=1,
        confidence=0.8,
    )


async def test_a_model_heal_counts_calls_whose_provider_reported_no_token_counts() -> None:
    run, heal = await verified()
    unreported = ModelUsage(
        provider="ollama",
        model="qwen3:4b-instruct-2507-q4_K_M",
        latency_ms=3_000,
        http_attempts=1,
        estimated_cost_usd=Decimal(0),
    )
    choose, repair = EVIDENCE.calls
    evidence = EVIDENCE.model_copy(
        update={"calls": (choose, repair.model_copy(update={"usage": unreported}))}
    )

    change = heal_change(as_model_heal(heal, evidence), run, promotion(run))

    assert change.model is not None
    assert (
        change.model.calls,
        change.model.input_tokens,
        change.model.output_tokens,
        change.model.unreported_token_calls,
    ) == (2, 400, 50, 1)


async def test_an_approved_model_pick_found_again_takes_its_usage_from_the_proposal() -> None:
    run, heal = await verified()
    found_again = as_model_heal(heal, EVIDENCE.model_copy(update={"calls": ()}))
    proposed = proposal(rung=3).model_copy(update={"model": EVIDENCE})
    run = run.model_copy(update={"proposals": (ProposalRecord(proposal=proposed),)})

    change = heal_change(found_again, run, promotion(run))

    assert change.model is not None
    assert change.model.calls == 2


async def test_a_model_heal_without_any_call_records_no_usage() -> None:
    run, heal = await verified()

    assert heal_change(as_model_heal(heal, None), run, promotion(run)).model is None


async def test_a_step_without_a_target_has_no_heal_to_record() -> None:
    run, heal = await verified()
    navigate = ledger_version().steps[0]

    with pytest.raises(MendworkError, match="only a step with a target can be healed"):
        heal_change(replace(heal, step=navigate), run, promotion(run))
