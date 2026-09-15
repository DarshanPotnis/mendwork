"""Which heals of a finished run may become versions, and what else the run proved (ADR 0013)."""

from datetime import UTC, datetime
from typing import Final

import pytest

from mendwork.engine.domain.approvals import (
    DecisionKind,
    ProposalDecision,
    ProposalOutcome,
    ProposalRecord,
)
from mendwork.engine.domain.changes import ApprovalReference
from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.patches import CaptureProblem, FoundTarget, PatchResult, WorkflowSource
from mendwork.engine.domain.runs import Run, RunStatus
from mendwork.engine.patching.eligibility import approval_for, run_patch_facts
from tests.unit.healing.builders import EXPORT_TEST_ID
from tests.unit.patching.builders import (
    DIFFERS,
    RECORDED_NAME,
    RENAMED,
    irreversible_ledger,
    ledger,
    ledger_page,
    ledger_version,
    result,
)
from tests.unit.replay.approval_builders import proposal

pytestmark = pytest.mark.asyncio

DECIDED: Final = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)


async def healed_run() -> Run:
    made = await ledger()
    return await made.run(ledger_page(), ledger_version())


async def test_a_succeeded_run_s_verified_heal_qualifies_with_the_captured_fingerprint() -> None:
    run = await healed_run()

    facts = run_patch_facts(run, ledger_version())

    [heal] = facts.heals
    assert (heal.index, heal.step.id, heal.rung, heal.approval) == (1, "export", 2, None)
    assert heal.found.accessible_name == RENAMED
    assert heal.found.selectors == (EXPORT_TEST_ID,)
    assert (facts.refused, facts.first_tries, facts.recorded) == ((), (), ())


@pytest.mark.parametrize(
    ("source", "detail"),
    [
        (None, "the run has no workflow source"),
        (DIFFERS, "this run does not save heals (file_differs)"),
    ],
)
async def test_heals_of_a_run_that_does_not_save_them_are_reported_as_not_saved(
    source: WorkflowSource | None, detail: str
) -> None:
    run = (await healed_run()).model_copy(update={"source": source})

    facts = run_patch_facts(run, ledger_version())

    assert facts.heals == ()
    [outcome] = facts.refused
    assert (outcome.step_id, outcome.result, outcome.rung, outcome.strength, outcome.detail) == (
        "export",
        PatchResult.NOT_SAVED,
        2,
        VerificationStrength.STRONG,
        detail,
    )


async def test_no_heal_of_a_run_that_did_not_succeed_is_saved() -> None:
    run = (await healed_run()).model_copy(update={"status": RunStatus.FAILED})

    [outcome] = run_patch_facts(run, ledger_version()).refused

    assert (outcome.result, outcome.detail) == (
        PatchResult.RUN_NOT_SUCCEEDED,
        "the run ended failed, so none of its heals is saved",
    )


async def test_a_heal_whose_element_could_not_be_fingerprinted_is_reported() -> None:
    run = await healed_run()
    step = result(run, "export").model_copy(
        update={"found": FoundTarget(problem=CaptureProblem.NO_SELECTOR)}
    )
    run = run.model_copy(update={"steps": (run.steps[0], step)})

    [outcome] = run_patch_facts(run, ledger_version()).refused

    assert (outcome.result, outcome.detail) == (
        PatchResult.NOT_CAPTURABLE,
        "the healed element could not be fingerprinted (no_selector)",
    )


async def test_an_irreversible_heal_qualifies_only_with_an_approval_that_acted_verified() -> None:
    run = await healed_run()
    workflow = irreversible_ledger()
    approved = ProposalDecision(kind=DecisionKind.APPROVED, at=DECIDED, audit_sequence=7)

    [refused] = run_patch_facts(run, workflow).refused
    assert refused.result is PatchResult.NOT_APPROVED
    for outcome in (ProposalOutcome.NOT_NEEDED, ProposalOutcome.ACTED_UNVERIFIED, None):
        record = ProposalRecord(proposal=proposal(), decision=approved, outcome=outcome)
        assert (
            run_patch_facts(run.model_copy(update={"proposals": (record,)}), workflow).heals == ()
        )

    verified = ProposalRecord(
        proposal=proposal(), decision=approved, outcome=ProposalOutcome.ACTED_VERIFIED
    )
    [heal] = run_patch_facts(run.model_copy(update={"proposals": (verified,)}), workflow).heals

    assert heal.approval == ApprovalReference(
        proposal_id="export-1", audit_sequence=7, decided_at=DECIDED
    )


async def test_a_rejection_or_another_step_s_approval_is_no_approval() -> None:
    run = await healed_run()
    rejected = ProposalDecision(kind=DecisionKind.REJECTED, at=DECIDED, audit_sequence=7)
    approved = ProposalDecision(kind=DecisionKind.APPROVED, at=DECIDED, audit_sequence=8)
    records = (
        ProposalRecord(proposal=proposal(), decision=rejected),
        ProposalRecord(
            proposal=proposal(step_id="other", index=0),
            decision=approved,
            outcome=ProposalOutcome.ACTED_VERIFIED,
        ),
    )

    assert approval_for(run.model_copy(update={"proposals": records}), "export") is None


async def test_pending_first_tries_and_steps_found_on_their_recorded_target_are_collected() -> None:
    made = await ledger()
    run = await made.run(ledger_page(RECORDED_NAME), ledger_version())

    facts = run_patch_facts(run, ledger_version())

    assert [step.id for step in facts.recorded] == ["export"]
    step = result(run, "export")
    assert step.target is not None
    tried = step.model_copy(
        update={"target": step.target.model_copy(update={"pending_patch": "0123456789abcdef"})}
    )
    facts = run_patch_facts(
        run.model_copy(update={"steps": (run.steps[0], tried)}), ledger_version()
    )
    assert [(item.pending_id, item.step.id) for item in facts.first_tries] == [
        ("0123456789abcdef", "export")
    ]
    assert facts.recorded == ()
