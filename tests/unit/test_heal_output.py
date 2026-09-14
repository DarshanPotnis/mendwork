"""Heal output: what each rung examined, why a heal was accepted, and the next step after a stop."""

import asyncio
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from pydantic import JsonValue

from mendwork.apps.cli.exit_codes import ExitCode, exit_code_for
from mendwork.apps.cli.heal_output import (
    attempt_lines,
    next_step,
    restored_lines,
    stop_headline,
    verified_lines,
)
from mendwork.apps.cli.human_output import (
    HumanProgress,
    describe_resolution,
    failure_lines,
    render_summary,
)
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.events import (
    HealAttemptedEvent,
    HealVerifiedEvent,
    StateRestoredEvent,
)
from mendwork.engine.domain.heals import (
    AbstentionReason,
    CandidateOrigin,
    FeatureScores,
    HealAttemptReport,
    HealProposal,
    HealReport,
    RecoveryReport,
    RejectionReason,
    RungOutcome,
    SafetyRejection,
    ScoredCandidate,
)
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.runs import (
    CheckpointResult,
    ErrorCategory,
    ErrorReport,
    Run,
    RunStatus,
    StepResult,
    StepStatus,
    parse_run_id,
)
from mendwork.engine.domain.targets import (
    IdentityReport,
    SelectorOutcome,
    SelectorReport,
    TargetEvidence,
)

RUN_ID: Final = parse_run_id("20260913T101500Z-5aa0c1de")
AT: Final = datetime(2026, 9, 13, 10, 15, tzinfo=UTC)
FEATURES: Final = FeatureScores(
    name=1,
    label=1,
    attributes=1,
    role=0.5,
    tag_type=0,
    nearby_text=1,
    structural_path=0.75,
    position=1,
)
LINK: Final = IdentityReport(tag="a", role="link", name="Export ledger", confirmed=True)
NEIGHBOUR: Final = IdentityReport(tag="button", role="button", name="Invite teammate")


def candidate(
    candidate_id: str,
    identity: IdentityReport,
    score: float,
    rejection: SafetyRejection | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        id=candidate_id,
        origin=CandidateOrigin.PAGE,
        identity=identity,
        score=score,
        features=FEATURES,
        rejection=rejection,
    )


def abstained(reason: str, **context: JsonValue) -> ErrorReport:
    return ErrorReport(
        type="HealAbstained",
        message="nothing was safe to act on",
        category=ErrorCategory.STEP,
        context={"reason": reason, **context},
    )


GUIDANCE: Final = {
    AbstentionReason.NO_CANDIDATES: (
        "Next: the control this step needs is not on the page. Check the page by hand; if it "
        "moved to another page or was replaced, re-record this step."
    ),
    AbstentionReason.BELOW_THRESHOLD: (
        "Next: nothing on the page matches the recording closely enough. If one of the "
        "candidates above is the right control, re-record this step on the current page. Do not "
        "lower MENDWORK_HEAL_ACCEPT_THRESHOLD to get past one page: it applies to every heal."
    ),
    AbstentionReason.BELOW_MARGIN: (
        "Next: several controls match about equally well, so choosing would be a guess. "
        "Re-record this step on the current page; the recorder scopes a repeated control to its "
        "row or section."
    ),
    AbstentionReason.TOP_REJECTED: (
        "Next: look at the page. The control most like the recorded one was refused by a safety "
        "rule (danger word); if the change is intended, re-record this step. Safety rules are "
        "never relaxed to get past it."
    ),
    AbstentionReason.CANDIDATE_CAP_REACHED: (
        "Next: raise MENDWORK_HEAL_CANDIDATES_MAX above 5120 (it is 4000) to let healing run on "
        "this page, or re-record this step."
    ),
    AbstentionReason.PAGE_NEVER_STABLE: (
        "Next: the page kept changing while it was examined. Re-run once it has finished "
        "loading, or re-record the step against the page in a stable state."
    ),
    AbstentionReason.UNVERIFIABLE: (
        "Next: add a checkpoint that proves what this step does (url_matches, element_visible, "
        "text_present, download_completed, or response_received), then re-run, or re-record the "
        "step."
    ),
    AbstentionReason.AUTHENTICATION_LIMIT: (
        "Next: sign in by hand to check the account is not locked, then re-record this step. "
        "Sign-in steps get one heal attempt per run."
    ),
    AbstentionReason.ATTEMPTS_EXHAUSTED: (
        "Next: every heal Mendwork tried failed this step's checkpoints. Re-record this step on "
        "the current page."
    ),
    AbstentionReason.RESTORE_FAILED: (
        "Next: re-run the workflow from the start; if it stops here again, re-record this step."
    ),
    AbstentionReason.HEAL_TIMED_OUT: (
        "Next: re-run; if the page is slow, raise MENDWORK_HEAL_TIMEOUT_MS, or re-record this step."
    ),
    AbstentionReason.MODEL_ABSTAINED: (
        "Next: neither the scoring nor the model could tell which control is the recorded one. "
        "If one of the candidates above is right, re-record this step on the current page."
    ),
    AbstentionReason.MODEL_CHOICE_OUT_OF_RANGE: (
        "Next: the model answered a number that was not on the list, which counts as no answer. "
        "Re-run; if it happens again, set MENDWORK_MODEL_NAME to another model, or re-record this "
        "step."
    ),
    AbstentionReason.MODEL_OUTPUT_INVALID: (
        "Next: the model twice replied in a form Mendwork cannot use (it was not exactly one JSON "
        "object). Check that local-chooser:4b supports JSON-schema output, or re-record this step."
    ),
    AbstentionReason.MODEL_UNAVAILABLE: (
        "Next: start Ollama (`ollama serve`) and check that `ollama list` shows local-chooser:4b, "
        "then re-run. It said: the provider could not be reached (ConnectError)."
    ),
    AbstentionReason.MODEL_BUDGET_EXHAUSTED: (
        "Next: today's 200 model calls (MENDWORK_MODEL_MAX_CALLS_PER_DAY) are used up; the count "
        "starts again at 2026-09-14T00:00:00+00:00. Re-record this step, or raise the limit if the "
        "spend is expected."
    ),
    AbstentionReason.MODEL_CHOICE_REFUSED: (
        "Next: look at the page. The model's choice was refused by a safety rule (danger word); if "
        "the change is intended, re-record this step. Safety rules are never relaxed to get past "
        "it."
    ),
}
CONTEXT: Final[dict[AbstentionReason, dict[str, JsonValue]]] = {
    AbstentionReason.TOP_REJECTED: {"rejection": "danger_word"},
    AbstentionReason.CANDIDATE_CAP_REACHED: {"on_page": 5120, "candidates_max": 4000},
    AbstentionReason.MODEL_OUTPUT_INVALID: {
        "model_problem": "it was not exactly one JSON object",
        "model_name": "local-chooser:4b",
    },
    AbstentionReason.MODEL_UNAVAILABLE: {
        "model_provider": "ollama",
        "model_name": "local-chooser:4b",
        "model_unavailable": "the provider could not be reached (ConnectError)",
    },
    AbstentionReason.MODEL_BUDGET_EXHAUSTED: {
        "budget_scope": "day",
        "budget_limit": 200,
        "budget_resets_at": "2026-09-14T00:00:00+00:00",
    },
    AbstentionReason.MODEL_CHOICE_REFUSED: {"rejection": "danger_word"},
}


def test_every_abstention_reason_has_pinned_guidance() -> None:
    assert set(GUIDANCE) == set(AbstentionReason)


@pytest.mark.parametrize("reason", list(AbstentionReason))
def test_the_guidance_line_is_chosen_by_the_abstention_reason(reason: AbstentionReason) -> None:
    assert next_step(abstained(reason.value, **CONTEXT.get(reason, {}))) == GUIDANCE[reason]


def test_approval_and_review_stops_have_their_own_next_steps() -> None:
    approval = ErrorReport(type="ApprovalRequired", message="m", category=ErrorCategory.STEP)
    review = ErrorReport(type="NeedsReview", message="m", category=ErrorCategory.STEP)

    assert next_step(approval) == (
        "Next: this step is irreversible, so its heal needs a person's approval. Review the "
        "proposal in the run's run.json; approving it with `mendwork approve` arrives with the "
        "approval flow (Phase 7). Until then, re-record the step if the proposal is right."
    )
    assert next_step(review) == (
        "Next: check in the application what the irreversible action did before running this "
        "workflow again; Mendwork will not retry it."
    )


def test_an_unfamiliar_reason_gets_general_guidance_and_other_failures_get_none() -> None:
    assert next_step(abstained("verified_heal_not_found")) == (
        "Next: re-run the workflow; if it stops here again, re-record this step."
    )
    other = ErrorReport(type="CheckpointFailed", message="m", category=ErrorCategory.STEP)
    assert next_step(other) is None


def test_an_abstention_explains_itself_and_ends_with_the_next_step() -> None:
    lines = failure_lines(abstained("below_margin"), None, action_performed=False)

    assert lines == [
        "      ABSTAINED: nothing was safe to act on",
        "      No action was performed on this step.",
        "      " + GUIDANCE[AbstentionReason.BELOW_MARGIN],
    ]


@pytest.mark.parametrize(
    ("error_type", "headline"),
    [
        ("HealAbstained", "ABSTAINED: why"),
        ("ApprovalRequired", "AWAITING APPROVAL: why"),
        ("NeedsReview", "NEEDS REVIEW: why"),
        ("CheckpointFailed", "FAILED CheckpointFailed: why"),
    ],
)
def test_the_headline_names_how_the_step_stopped(error_type: str, headline: str) -> None:
    error = ErrorReport(type=error_type, message="why", category=ErrorCategory.STEP)

    assert stop_headline(error) == headline


def rung0(outcome: RungOutcome, **target: object) -> HealAttemptReport:
    evidence = TargetEvidence.model_validate({"selectors": [], **target})
    return HealAttemptReport(rung=0, attempt=1, outcome=outcome, target=evidence)


def test_rung0_lines_say_why_the_recorded_selectors_could_not_proceed() -> None:
    drifted = rung0(RungOutcome.DRIFTED, identity=LINK.model_dump(), differences=["role"])

    assert attempt_lines(drifted) == [
        '      rung 0: the recorded selectors agree on link "Export ledger", but its role differs '
        "from the recording"
    ]
    assert attempt_lines(rung0(RungOutcome.AMBIGUOUS)) == [
        "      rung 0: the recorded selectors do not point at one element"
    ]
    assert attempt_lines(rung0(RungOutcome.NOT_FOUND)) == [
        "      rung 0: no recorded selector found a visible element"
    ]


def rung1(outcome: RungOutcome, count: int = 2, **fields: object) -> HealAttemptReport:
    reports = [
        SelectorReport(rank=rank, strategy="text", level_counts=(0,), outcome=SelectorOutcome.NONE)
        for rank in range(count)
    ]
    evidence = TargetEvidence(selectors=tuple(reports), identity=LINK, differences=("role",))
    return HealAttemptReport.model_validate(
        {"rung": 1, "attempt": 1, "outcome": outcome, "target": evidence, **fields}
    )


REFUSED_ONE: Final = SafetyRejection(
    reason=RejectionReason.DANGER_WORD, detail="its name adds danger"
)
RUNG1_LINES: Final = [
    (
        rung1(
            RungOutcome.RESOLVED,
            candidates=[candidate("c1", LINK, 0.9)],
            score=0.9,
            threshold=0.6,
        ),
        'rung 1: 2 alternate selectors found the recorded link "Export ledger" '
        "(score 0.90, needs 0.60)",
    ),
    (
        rung1(RungOutcome.DRIFTED),
        'rung 1: 2 alternate selectors agree on link "Export ledger", but its role differs; '
        "it is compared at rung 2",
    ),
    (
        rung1(RungOutcome.AMBIGUOUS, count=1),
        "rung 1: 1 alternate selector do not point at one element",
    ),
    (
        rung1(RungOutcome.TOP_REJECTED, candidates=[candidate("c1", LINK, 0.9, REFUSED_ONE)]),
        "rung 1: 2 alternate selectors found the recorded identity, but it was refused: its "
        "name adds danger; it is compared at rung 2",
    ),
    (
        rung1(RungOutcome.BELOW_THRESHOLD, score=0.5, threshold=0.6),
        "rung 1: 2 alternate selectors found the recorded identity, but it scored 0.50, below "
        "0.60; it is compared at rung 2",
    ),
    (
        rung1(RungOutcome.PAGE_NEVER_STABLE),
        "rung 1: the page kept changing while it was examined",
    ),
    (
        rung1(RungOutcome.NOT_FOUND, count=0),
        "rung 1: the fingerprint holds no selector the recording did not already try",
    ),
    (rung1(RungOutcome.NOT_FOUND, count=3), "rung 1: 3 alternate selectors found nothing"),
]


@pytest.mark.parametrize(("report", "line"), RUNG1_LINES)
def test_rung1_lines_say_what_the_alternate_selectors_found(
    report: HealAttemptReport, line: str
) -> None:
    assert attempt_lines(report) == ["      " + line]


def test_an_accepted_rung2_heal_shows_its_score_margin_features_and_kind_change() -> None:
    report = HealAttemptReport(
        rung=2,
        attempt=1,
        outcome=RungOutcome.RESOLVED,
        candidates=(candidate("c1", LINK, 0.88), candidate("c2", NEIGHBOUR, 0.39)),
        considered=10,
        on_page=12,
        chosen="c1",
        runner_up="c2",
        score=0.88,
        margin=0.49,
        threshold=0.6,
        required_margin=0.15,
        kind_change="button → link",
    )

    assert attempt_lines(report) == [
        "      rung 2: compared 10 candidates (12 on the page)",
        '        chose link "Export ledger" · score 0.88 (needs 0.60) · margin 0.49 over button '
        '"Invite teammate" (needs 0.15)',
        "        name 1.00 · label 1.00 · attributes 1.00 · role 0.50 · tag/type 0.00 "
        "· nearby 1.00 · path 0.75 · position 1.00",
        "        kind changed button → link: allowed because this step checks the effect of "
        "activating it",
    ]


def test_a_declined_rung2_lists_what_it_compared_and_what_was_refused() -> None:
    refused = SafetyRejection(reason=RejectionReason.DANGER_WORD, detail='its name adds "delete"')
    report = HealAttemptReport(
        rung=2,
        attempt=1,
        outcome=RungOutcome.TOP_REJECTED,
        candidates=(candidate("c1", LINK, 0.7, refused), candidate("c2", NEIGHBOUR, 0.39)),
        considered=1,
        on_page=1,
    )

    assert attempt_lines(report) == [
        "      rung 2: compared 1 candidate (1 on the page)",
        '        1. link "Export ledger" 0.70  REFUSED: its name adds "delete"',
        '        2. button "Invite teammate" 0.39',
    ]


def test_rung2_lines_for_a_capped_or_unstable_page() -> None:
    capped = HealAttemptReport(
        rung=2, attempt=1, outcome=RungOutcome.CANDIDATE_CAP_REACHED, on_page=5120
    )
    unstable = HealAttemptReport(rung=2, attempt=1, outcome=RungOutcome.PAGE_NEVER_STABLE)

    assert attempt_lines(capped) == [
        "      rung 2: the page has 5120 candidates, more than the configured limit, so healing "
        "does not run on it"
    ]
    assert attempt_lines(unstable) == [
        "      rung 2: the page kept changing while candidates were compared"
    ]


def stamp() -> dict[str, object]:
    return {"run_id": RUN_ID, "sequence": 3, "at": AT, "step_id": StepId("export"), "index": 1}


def test_verification_and_restore_lines() -> None:
    passed = HealVerifiedEvent(**stamp(), rung=2, attempt=1, passed=True)
    failed = HealVerifiedEvent(
        **stamp(),
        rung=2,
        attempt=1,
        passed=False,
        failed_checkpoint=CheckpointResult(
            index=0, kind="text_present", passed=False, reason="timeout"
        ),
    )
    restored = StateRestoredEvent(
        **stamp(),
        recovery=RecoveryReport(
            after_attempt=1,
            url="https://ledger.example.test/",
            replayed=("show",),
            cleared_field=True,
            restored=True,
        ),
    )
    refused = StateRestoredEvent(
        **stamp(),
        recovery=RecoveryReport(
            after_attempt=1,
            url="https://ledger.example.test/",
            replayed=(),
            cleared_field=False,
            restored=False,
            reason="it would repeat send",
        ),
    )

    assert verified_lines(passed) == [
        "      HEALED at rung 2: every checkpoint passed after acting on the healed target"
    ]
    assert verified_lines(failed) == [
        "      heal not verified: checkpoint 1 (text_present) failed, so that candidate is excluded"
    ]
    assert restored_lines(restored) == [
        "      restored the page: re-opened https://ledger.example.test/ and replayed show; "
        "cleared the field the failed attempt typed into"
    ]
    assert restored_lines(refused) == ["      could not restore the page: it would repeat send"]


def test_a_healed_target_line_names_the_rung_and_the_element() -> None:
    assert describe_resolution(TargetEvidence(selectors=(), identity=LINK, healed_rung=2)) == (
        'target: healed at rung 2 → link "Export ledger"'
    )


def test_progress_prints_heal_events_as_they_arrive() -> None:
    stream = io.StringIO()
    progress = HumanProgress(stream, Path("artifacts/runs"))
    report = HealAttemptReport(rung=0, attempt=1, outcome=RungOutcome.NOT_FOUND)

    asyncio.run(progress.emit(HealAttemptedEvent(**stamp(), report=report)))

    assert stream.getvalue() == "      rung 0: no recorded selector found a visible element\n"


def step_result(
    status: StepStatus, heal: HealReport | None, error: ErrorReport | None
) -> StepResult:
    return StepResult(
        step_id=StepId("export"),
        index=0,
        action=ActionType.CLICK,
        status=status,
        started_at=AT,
        duration_ms=120,
        error=error,
        heal=heal,
    )


def run_of(status: RunStatus, step: StepResult) -> Run:
    return Run(
        run_id=RUN_ID,
        workflow_id="ledger",
        workflow_version=1,
        status=status,
        started_at=AT,
        finished_at=AT,
        duration_ms=500,
        steps=(step,),
        error=step.error,
    )


def test_the_summary_shows_healed_abstained_and_stopped_steps() -> None:
    healed = step_result(StepStatus.SUCCEEDED, HealReport(healed_rung=2), None)
    abstain = step_result(
        StepStatus.FAILED,
        HealReport(abstention=AbstentionReason.BELOW_MARGIN),
        abstained("below_margin"),
    )
    proposal = HealProposal(
        rung=2, candidate=candidate("c1", LINK, 0.9), margin=0.4, reason="irreversible"
    )
    approval_error = ErrorReport(
        type="ApprovalRequired", message="needs approval", category=ErrorCategory.STEP
    )
    waiting = step_result(
        StepStatus.AWAITING_APPROVAL, HealReport(proposal=proposal), approval_error
    )
    review_error = ErrorReport(type="NeedsReview", message="check it", category=ErrorCategory.STEP)
    review = step_result(StepStatus.NEEDS_REVIEW, None, review_error)  # fmt: skip

    runs = Path("artifacts/runs")
    healed_summary = render_summary(run_of(RunStatus.SUCCEEDED, healed), runs)
    abstain_summary = render_summary(run_of(RunStatus.FAILED, abstain), runs)
    waiting_summary = render_summary(run_of(RunStatus.AWAITING_APPROVAL, waiting), runs)
    review_summary = render_summary(run_of(RunStatus.NEEDS_REVIEW, review), runs)

    assert " 1  export  click   healed r2  -            succeeded  0.12s" in healed_summary
    assert "abstained" in abstain_summary
    assert "ABSTAINED at step 1 export: HealAbstained" in abstain_summary
    assert "proposal" in waiting_summary
    assert "AWAITING APPROVAL at step 1 export: ApprovalRequired" in waiting_summary
    assert "NEEDS REVIEW at step 1 export: NeedsReview" in review_summary


def test_runs_stopped_for_a_person_exit_4() -> None:
    waiting = step_result(StepStatus.AWAITING_APPROVAL, None, None)

    assert exit_code_for(run_of(RunStatus.AWAITING_APPROVAL, waiting)) is ExitCode.NEEDS_PERSON
    assert exit_code_for(run_of(RunStatus.NEEDS_REVIEW, waiting)) is ExitCode.NEEDS_PERSON
    assert int(ExitCode.NEEDS_PERSON) == 4
