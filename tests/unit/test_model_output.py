"""Rung 3 in readable output: what the model was shown and chose, its cost, and what to do next."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest
from pydantic import JsonValue

from mendwork.apps.cli.heal_output import attempt_lines, resolution_line, verified_lines
from mendwork.apps.cli.human_output import render_summary
from mendwork.apps.cli.model_output import model_next_step, model_usage_line, usage_summary
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.events import HealVerifiedEvent
from mendwork.engine.domain.heals import (
    CandidateOrigin,
    FeatureScores,
    HealAttemptReport,
    HealReport,
    RejectionReason,
    RungOutcome,
    SafetyRejection,
    ScoredCandidate,
)
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.model_evidence import (
    BudgetScope,
    BudgetStop,
    CandidateDescription,
    ModelCall,
    ModelCallOutcome,
    ModelCallPurpose,
    ModelChoiceEvidence,
    ModelUsage,
    ModelUsageTotals,
    ShownCandidate,
)
from mendwork.engine.domain.runs import Run, RunStatus, StepResult, StepStatus, parse_run_id
from mendwork.engine.domain.targets import IdentityReport, TargetEvidence

RUN_ID: Final = parse_run_id("20260913T101500Z-5aa0c1de")
AT: Final = datetime(2026, 9, 13, 10, 15, tzinfo=UTC)
FEATURES: Final = FeatureScores(
    name=0,
    label=0,
    attributes=0.5,
    role=1,
    tag_type=1,
    nearby_text=1,
    structural_path=1,
    position=1,
)
DOWNLOAD: Final = IdentityReport(tag="button", role="button", name="Quarterly download")
SHOWN: Final = (
    ShownCandidate(
        number=1,
        candidate="c1",
        description=CandidateDescription(kind="button", name="Quarterly download"),
        similarity=0.58,
    ),
    ShownCandidate(
        number=2,
        candidate="c3",
        description=CandidateDescription(kind="button", name="Export summary"),
        similarity=0.35,
    ),
)


def usage(cost: Decimal | None = Decimal(0), latency_ms: int = 2_410) -> ModelUsage:
    return ModelUsage(
        provider="ollama",
        model="local-chooser:4b",
        input_tokens=612,
        output_tokens=38,
        latency_ms=latency_ms,
        http_attempts=1,
        estimated_cost_usd=cost,
    )


def answered(purpose: ModelCallPurpose = ModelCallPurpose.CHOOSE) -> ModelCall:
    return ModelCall(purpose=purpose, outcome=ModelCallOutcome.ANSWERED, usage=usage())


def report(
    outcome: RungOutcome,
    *,
    rejection: SafetyRejection | None = None,
    **evidence: object,
) -> HealAttemptReport:
    values: dict[str, object] = {
        "prompt_version": "choose-candidate/1",
        "shown": SHOWN,
        "ineligible": 3,
        "calls": (answered(),),
        "choice": 1,
        "confidence": 0.9,
        "reason": "It is the same export, renamed.",
        **evidence,
    }
    return HealAttemptReport(
        rung=3,
        attempt=1,
        outcome=outcome,
        candidates=(
            ScoredCandidate(
                id="c1",
                origin=CandidateOrigin.PAGE,
                identity=DOWNLOAD,
                score=0.58,
                features=FEATURES,
                rejection=rejection,
            ),
        ),
        considered=2,
        model=ModelChoiceEvidence.model_validate(values),
    )


ASKED: Final = (
    "      rung 3: asked ollama local-chooser:4b to choose among 2 candidates "
    "(3 other candidates not shown)"
)
REASON: Final = '"It is the same export, renamed."'
CHOSE: Final = f'        model chose 1. button "Quarterly download" · confidence 0.90 · {REASON}'
COST: Final = "        1 call · 612 tokens in, 38 out · 2.41 s · est. $0.00"


def test_an_accepted_choice_shows_what_was_asked_chosen_and_why_and_that_checkpoints_decide() -> (
    None
):
    assert attempt_lines(report(RungOutcome.RESOLVED)) == [
        ASKED,
        CHOSE,
        COST,
        "        its choice passed every safety rule and Playwright confirmed it; the step's "
        "checkpoints decide whether it was right",
    ]


def test_a_null_answer_says_the_model_chose_none() -> None:
    lines = attempt_lines(report(RungOutcome.MODEL_ABSTAINED, choice=None, reason="None fit."))

    assert lines == [
        ASKED,
        '        model chose none of them · confidence 0.90 · "None fit."',
        COST,
    ]


def test_a_refused_choice_says_which_rule_refused_it() -> None:
    refused = SafetyRejection(reason=RejectionReason.DANGER_WORD, detail='its name adds "delete"')

    lines = attempt_lines(report(RungOutcome.CHOICE_REFUSED, rejection=refused))

    assert lines[-1] == '        REFUSED: its name adds "delete"'


def test_unusable_replies_and_an_unreachable_provider_are_shown() -> None:
    invalid = ModelCall(
        purpose=ModelCallPurpose.CHOOSE,
        outcome=ModelCallOutcome.INVALID_OUTPUT,
        problem="it was not exactly one JSON object",
        usage=usage(),
    )
    down = ModelCall(
        purpose=ModelCallPurpose.CHOOSE,
        outcome=ModelCallOutcome.UNAVAILABLE,
        problem="connection",
        usage=usage(latency_ms=12),
    )

    twice = attempt_lines(
        report(
            RungOutcome.OUTPUT_INVALID,
            calls=(invalid, invalid),
            choice=None,
            reason=None,
            confidence=None,
        )
    )
    unreachable = attempt_lines(
        report(
            RungOutcome.MODEL_UNAVAILABLE,
            calls=(down,),
            choice=None,
            reason=None,
            confidence=None,
            unavailable="the provider could not be reached (ConnectError)",
        )
    )

    assert twice[1:3] == ["        reply could not be used: it was not exactly one JSON object"] * 2
    assert twice[3] == "        2 calls · 1224 tokens in, 76 out · 4.82 s · est. $0.00"
    assert unreachable[1] == (
        "        the model could not answer: the provider could not be reached (ConnectError)"
    )


@pytest.mark.parametrize(
    ("outcome", "line"),
    [
        (
            RungOutcome.NO_ELIGIBLE,
            "rung 3: no candidate both passed the safety rules and shared wording or identity "
            "attributes with the recording, so the model was not asked",
        ),
        (
            RungOutcome.LOOK_ALIKES,
            "rung 3: the closest candidates read exactly alike, so a model could only guess "
            "between them and was not asked",
        ),
        (
            RungOutcome.NOT_ASKED,
            "rung 3: the model was not asked, because this step cannot act on a heal",
        ),
    ],
)
def test_a_rung_that_did_not_ask_says_why_in_one_line(outcome: RungOutcome, line: str) -> None:
    assert attempt_lines(report(outcome, shown=(), calls=())) == ["      " + line]


def test_a_budget_used_up_before_any_call_is_one_line() -> None:
    stop = BudgetStop(
        scope=BudgetScope.RUN, limit=4, detail="this run has used all 4 model calls it may make"
    )

    lines = attempt_lines(report(RungOutcome.BUDGET_EXHAUSTED, calls=(), budget=stop))

    assert lines == [
        "      rung 3: the model was not asked: this run has used all 4 model calls it may make"
    ]


def test_an_out_of_range_answer_and_a_changed_page_explain_themselves() -> None:
    out_of_range = attempt_lines(report(RungOutcome.CHOICE_OUT_OF_RANGE, choice=7))
    changed = attempt_lines(report(RungOutcome.PAGE_NEVER_STABLE))

    assert out_of_range[1] == (
        f"        model chose 7, which is not on the list · confidence 0.90 · {REASON}"
    )
    assert out_of_range[-1] == "        that number is not on the list, which counts as no answer"
    assert (
        changed[-1]
        == "        the page changed while the model was choosing, so its choice was not used"
    )


@pytest.mark.parametrize(
    ("totals", "line"),
    [
        (ModelUsageTotals(), None),
        (
            ModelUsageTotals(
                calls=1,
                input_tokens=431,
                output_tokens=24,
                latency_ms=900,
                estimated_cost_usd=Decimal("0.0000527"),
            ),
            "Model: 1 call · 431 tokens in, 24 out · 0.90 s · est. $0.000053",
        ),
        (
            ModelUsageTotals(
                calls=2, input_tokens=800, output_tokens=40, latency_ms=2_000, unpriced_calls=2
            ),
            "Model: 2 calls · 800 tokens in, 40 out · 2.00 s · cost unknown for 2 calls "
            "(no price in MENDWORK_MODEL_PRICES)",
        ),
        (
            ModelUsageTotals(
                calls=2,
                input_tokens=800,
                output_tokens=40,
                latency_ms=2_000,
                estimated_cost_usd=Decimal("0.25"),
                unpriced_calls=1,
            ),
            "Model: 2 calls · 800 tokens in, 40 out · 2.00 s · est. $0.25 for 1 of 2 calls, cost "
            "unknown for 1 (no price in MENDWORK_MODEL_PRICES)",
        ),
        (
            ModelUsageTotals(calls=1, latency_ms=900, unreported_token_calls=1),
            "Model: 1 call · token counts not reported · 0.90 s · est. $0.00",
        ),
        (
            ModelUsageTotals(
                calls=2,
                input_tokens=431,
                output_tokens=24,
                latency_ms=1_800,
                unreported_token_calls=1,
            ),
            "Model: 2 calls · 431 tokens in, 24 out for 1 of 2 calls, not for 1 · 1.80 s · "
            "est. $0.00",
        ),
    ],
)
def test_the_runs_model_usage_line(totals: ModelUsageTotals, line: str | None) -> None:
    assert model_usage_line(totals) == line


def test_usage_of_no_calls_is_zero() -> None:
    assert usage_summary(()) == (
        "0 calls · 0 tokens in, 0 out · 0.00 s · cost unknown for 0 calls "
        "(no price in MENDWORK_MODEL_PRICES)"
    )


@pytest.mark.parametrize(
    ("reason", "context", "line"),
    [
        (
            "model_budget_exhausted",
            {"budget_scope": "run", "budget_limit": 4},
            "Next: this run used all 4 model calls MENDWORK_MODEL_MAX_CALLS_PER_RUN allows. "
            "Re-record the steps that needed healing; raise the limit only if this many model "
            "choices in one run is expected.",
        ),
        (
            "model_budget_exhausted",
            {"budget_scope": "day", "budget_limit": 200},
            "Next: the model usage ledger (under MENDWORK_ARTIFACTS_DIR/usage) could not be read, "
            "so no call was made. Fix or remove the file it names, then re-run.",
        ),
        (
            "model_unavailable",
            {"model_provider": "gemini", "model_unavailable": "the provider answered HTTP 403"},
            "Next: check MENDWORK_MODEL_BASE_URL, MENDWORK_MODEL_API_KEY, and MENDWORK_MODEL_NAME, "
            "then re-run. The provider said: the provider answered HTTP 403.",
        ),
        (
            "model_choice_refused",
            {"rejection": "weak_verification"},
            "Next: this step's checkpoints only check where it leads or what the field holds, so a "
            "model's choice must keep the recorded id, name, or test id. Add a checkpoint that "
            "observes the step's own effect (element_visible or text_present) to the workflow, or "
            "re-record this step.",
        ),
        (
            "model_choice_refused",
            {"rejection": "context_lost"},
            "Next: look at the page. The model's choice was refused by a safety rule (context "
            "lost); if the change is intended, re-record this step. Safety rules are never relaxed "
            "to get past it.",
        ),
        ("below_threshold", {}, None),
    ],
)
def test_next_steps_for_budgets_hosted_providers_and_other_reasons(
    reason: str, context: dict[str, JsonValue], line: str | None
) -> None:
    assert model_next_step(reason, context) == line


def stamp() -> dict[str, object]:
    return {"run_id": RUN_ID, "sequence": 3, "at": AT, "step_id": StepId("export"), "index": 1}


def test_a_verified_model_choice_says_the_checkpoints_decided() -> None:
    event = HealVerifiedEvent(**stamp(), rung=3, attempt=1, passed=True)

    assert verified_lines(event) == [
        "      HEALED at rung 3 (model choice): every checkpoint passed after acting on the "
        "model's choice; the checkpoints, not the model, decided"
    ]
    assert resolution_line(TargetEvidence(selectors=(), identity=DOWNLOAD, healed_rung=3)) == (
        'target: healed at rung 3 (model choice) → button "Quarterly download"'
    )


def test_the_summary_marks_a_model_heal_and_adds_the_runs_model_usage() -> None:
    healed = StepResult(
        step_id=StepId("export"),
        index=0,
        action=ActionType.CLICK,
        status=StepStatus.SUCCEEDED,
        started_at=AT,
        duration_ms=2_600,
        heal=HealReport(healed_rung=3),
    )
    run = Run(
        run_id=RUN_ID,
        workflow_id="ledger",
        workflow_version=1,
        status=RunStatus.SUCCEEDED,
        started_at=AT,
        finished_at=AT,
        duration_ms=3_000,
        steps=(healed,),
        model_usage=ModelUsageTotals(calls=1, input_tokens=612, output_tokens=38, latency_ms=2_410),
    )

    summary = render_summary(run, Path("artifacts/runs"))

    assert "healed r3 (model)" in summary
    assert summary.endswith("Model: 1 call · 612 tokens in, 38 out · 2.41 s · est. $0.00")
