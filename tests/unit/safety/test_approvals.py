"""Approval rules: who may decide, which runs may resume, whether the page still shows the approved
element, and what came of an approval."""

import pytest

from mendwork.engine.domain.approvals import (
    DecisionKind,
    ProposalOutcome,
    ProposalRecord,
    ResumeState,
    StaleReason,
)
from mendwork.engine.domain.heals import AbstentionReason, HealedRung, HealReport
from mendwork.engine.domain.runs import RunStatus, StepResult, StepStatus
from mendwork.engine.domain.targets import TargetEvidence
from mendwork.engine.safety.approvals import (
    NotPending,
    NotResumable,
    Problem,
    Settled,
    approval_mismatch,
    approval_stale,
    decision_problem,
    record_problem,
    settled,
    snapshot_problem,
    stale_reason_for,
)
from tests.unit.replay.approval_builders import (
    DIGEST,
    IDENTITY,
    RUN_ID,
    SIGNATURE,
    decision,
    error,
    ledger_workflow,
    paused_run,
    paused_step,
    proposal,
    result,
)


def test_a_pending_proposal_of_a_paused_run_may_take_a_decision() -> None:
    assert decision_problem(paused_run(), "export-1") is None


def test_an_unknown_proposal_is_refused_naming_the_runs_proposals() -> None:
    assert decision_problem(paused_run(), "export-9") == Problem(
        NotPending.UNKNOWN_PROPOSAL,
        f"run {RUN_ID} has no proposal export-9 (its proposals: export-1)",
    )
    none = decision_problem(paused_run(proposals=()), "export-1")
    assert none is not None
    assert none.message.endswith("(its proposals: none)")


def test_a_decided_proposal_is_refused_with_the_audit_entry_that_decided_it() -> None:
    decided = ProposalRecord(proposal=proposal(), decision=decision(sequence=3))

    assert decision_problem(paused_run(proposals=(decided,)), "export-1") == Problem(
        NotPending.ALREADY_DECIDED, "proposal export-1 was already approved (audit entry 3)"
    )


def test_a_run_no_longer_awaiting_approval_takes_no_decision() -> None:
    assert decision_problem(paused_run(status=RunStatus.CANCELLED), "export-1") == Problem(
        NotPending.RUN_NOT_AWAITING_APPROVAL, f"run {RUN_ID} is cancelled, not awaiting approval"
    )


def test_a_paused_version_2_record_with_recoverable_inputs_may_resume() -> None:
    assert record_problem(paused_run(), proposal()) is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"record_version": 1}, NotResumable.RECORD_VERSION),
        ({"resume": None}, NotResumable.INPUTS_NOT_RECOVERABLE),
        ({"resume": ResumeState(inputs_recoverable=False)}, NotResumable.INPUTS_NOT_RECOVERABLE),
        (
            {"steps": (result("open", 0, StepStatus.FAILED), paused_step())},
            NotResumable.EARLIER_STEP_NOT_SUCCEEDED,
        ),
    ],
)
def test_a_record_that_cannot_resume_says_why(
    overrides: dict[str, object], reason: NotResumable
) -> None:
    problem = record_problem(paused_run(**overrides), proposal())

    assert problem is not None
    assert problem.reason == reason


def test_an_unchanged_workflow_with_only_reversible_steps_before_the_approved_one_resumes() -> None:
    workflow = ledger_workflow(("export", "irreversible"))

    assert snapshot_problem(paused_run(), workflow, DIGEST, proposal()) is None


def test_a_workflow_that_changed_after_the_run_paused_cannot_resume() -> None:
    workflow = ledger_workflow(("export", "irreversible"))

    assert snapshot_problem(paused_run(), workflow, "b" * 64, proposal()) == Problem(
        NotResumable.WORKFLOW_CHANGED,
        f"the workflow saved with run {RUN_ID} changed after the run paused",
    )


def test_an_irreversible_step_before_the_approved_one_would_run_again_so_it_cannot_resume() -> None:
    workflow = ledger_workflow(("submit", "irreversible"), ("export", "irreversible"))

    problem = snapshot_problem(paused_run(), workflow, DIGEST, proposal(index=2))

    assert problem is not None
    assert problem.reason == NotResumable.EARLIER_IRREVERSIBLE_STEP
    assert "step 2 submit is irreversible, so its action would run again" in problem.message


@pytest.mark.parametrize(
    ("rung", "abstention", "reason"),
    [
        (2, None, StaleReason.APPROVED_TARGET_NOT_FOUND),
        (2, AbstentionReason.NO_CANDIDATES, StaleReason.APPROVED_TARGET_NOT_FOUND),
        (2, AbstentionReason.PAGE_NEVER_STABLE, StaleReason.PAGE_NEVER_STABLE),
        (2, AbstentionReason.BELOW_THRESHOLD, StaleReason.NO_LONGER_ACCEPTED),
        (1, AbstentionReason.BELOW_MARGIN, StaleReason.NO_LONGER_ACCEPTED),
        (2, AbstentionReason.TOP_REJECTED, StaleReason.REFUSED),
        (3, AbstentionReason.MODEL_CHOICE_REFUSED, StaleReason.REFUSED),
        (3, AbstentionReason.BELOW_THRESHOLD, StaleReason.APPROVED_TARGET_NOT_FOUND),
    ],
)
def test_why_nothing_accepted_is_stale_depends_on_the_abstention_and_the_proposals_rung(
    rung: HealedRung, abstention: AbstentionReason | None, reason: StaleReason
) -> None:
    assert stale_reason_for(proposal(rung=rung), abstention) is reason


def test_the_approved_element_matches_on_its_identity_and_confirmed_role_and_name() -> None:
    assert approval_mismatch(proposal(), identity=SIGNATURE, confirmed=IDENTITY) is None


def test_another_element_or_a_changed_confirmed_identity_does_not_match() -> None:
    elsewhere = (*SIGNATURE[:-1], "Annual ledger")
    renamed = IDENTITY.model_copy(update={"name": "Export ledger now"})
    unconfirmed = IDENTITY.model_copy(update={"confirmed": False})

    assert approval_mismatch(proposal(), identity=elsewhere, confirmed=IDENTITY) is (
        StaleReason.DIFFERENT_TARGET
    )
    assert approval_mismatch(proposal(), identity=SIGNATURE, confirmed=renamed) is (
        StaleReason.IDENTITY_CHANGED
    )
    assert approval_mismatch(proposal(), identity=SIGNATURE, confirmed=unconfirmed) is (
        StaleReason.IDENTITY_CHANGED
    )


def test_a_stale_approval_says_what_no_longer_matched_and_that_nothing_acted() -> None:
    stale = approval_stale(proposal(), StaleReason.DIFFERENT_TARGET, detail="step 1 open failed")
    plain = approval_stale(proposal(), StaleReason.PAGE_NEVER_STABLE)

    assert stale.message == (
        "the page no longer matches proposal export-1: the heal ladder now finds a different "
        "element; nothing was acted on"
    )
    assert stale.context == {
        "reason": "approval_stale",
        "stale_reason": "different_target",
        "proposal_id": "export-1",
        "detail": "step 1 open failed",
    }
    assert "detail" not in plain.context


HEALED_TARGET = TargetEvidence(selectors=(), healed_rung=2)


@pytest.mark.parametrize(
    ("step", "outcome"),
    [
        (
            result("export", 1, StepStatus.SUCCEEDED, heal=HealReport(healed_rung=2)),
            Settled(ProposalOutcome.ACTED_VERIFIED),
        ),
        (result("export", 1, StepStatus.SUCCEEDED), Settled(ProposalOutcome.NOT_NEEDED)),
        (result("export", 1, StepStatus.NEEDS_REVIEW), Settled(ProposalOutcome.ACTED_UNVERIFIED)),
        (result("export", 1, StepStatus.CANCELLED), Settled(ProposalOutcome.INTERRUPTED)),
        (result("export", 1, StepStatus.NOT_RUN), Settled(ProposalOutcome.INTERRUPTED)),
        (
            result(
                "export",
                1,
                StepStatus.FAILED,
                failure=error("ApprovalStale", "gone", stale_reason="identity_changed"),
            ),
            Settled(ProposalOutcome.STALE, StaleReason.IDENTITY_CHANGED, "gone"),
        ),
        (
            result(
                "export",
                1,
                StepStatus.FAILED,
                failure=error("NavigationError", "blocked"),
                action_performed=True,
                target=HEALED_TARGET,
            ),
            Settled(ProposalOutcome.ACTED_UNVERIFIED, detail="NavigationError: blocked"),
        ),
        (
            result(
                "export",
                1,
                StepStatus.FAILED,
                failure=error("CheckpointFailed", "no banner"),
                action_performed=True,
            ),
            Settled(ProposalOutcome.NOT_NEEDED, detail="CheckpointFailed: no banner"),
        ),
        (
            result(
                "export", 1, StepStatus.FAILED, failure=error("PageNeverStable", "kept changing")
            ),
            Settled(
                ProposalOutcome.STALE,
                StaleReason.PAGE_NEVER_STABLE,
                "PageNeverStable: kept changing",
            ),
        ),
        (
            result(
                "export", 1, StepStatus.FAILED, failure=error("TargetNotActionable", "disabled")
            ),
            Settled(
                ProposalOutcome.STALE,
                StaleReason.APPROVED_TARGET_NOT_FOUND,
                "TargetNotActionable: disabled",
            ),
        ),
        (
            result("export", 1, StepStatus.AWAITING_APPROVAL),
            Settled(ProposalOutcome.STALE, StaleReason.APPROVED_TARGET_NOT_FOUND),
        ),
    ],
)
def test_what_came_of_an_approval_follows_from_the_approved_steps_result(
    step: StepResult, outcome: Settled
) -> None:
    assert settled(step) == outcome


def test_decisions_are_named_by_their_kind() -> None:
    assert [kind.value for kind in DecisionKind] == ["approved", "rejected"]
