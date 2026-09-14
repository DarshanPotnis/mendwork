"""Rung 3 abstentions in words, with every fact behind them in the context."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import pytest

from mendwork.engine.domain.heals import (
    AbstentionReason,
    CandidateOrigin,
    FeatureScores,
    HealAttemptReport,
    RejectionReason,
    RungOutcome,
    SafetyRejection,
    ScoredCandidate,
)
from mendwork.engine.domain.model_evidence import (
    BudgetScope,
    BudgetStop,
    CandidateDescription,
    ModelCall,
    ModelCallOutcome,
    ModelCallPurpose,
    ModelChoiceEvidence,
    ModelUsage,
    ShownCandidate,
)
from mendwork.engine.domain.targets import IdentityReport
from mendwork.engine.healing.explain import abstained, abstention_context, abstention_message
from tests.unit.replay.builders import healing

CONFIG: Final = healing()
FEATURES: Final = FeatureScores.model_validate(
    dict.fromkeys(("role", "tag_type", "nearby_text", "structural_path", "position"), 1.0)
    | {"name": 0.0, "label": 0.0, "attributes": 0.5}
)
SHOWN: Final = tuple(
    ShownCandidate(
        number=number,
        candidate=f"c{number}",
        description=CandidateDescription(kind="button", name=name),
        similarity=0.5,
    )
    for number, name in ((1, "Quarterly download"), (2, "Export summary"))
)
USAGE: Final = ModelUsage(
    provider="ollama",
    model="local-chooser:4b",
    input_tokens=600,
    output_tokens=40,
    latency_ms=3_000,
    http_attempts=1,
    estimated_cost_usd=Decimal(0),
)
REFUSED: Final = SafetyRejection(
    reason=RejectionReason.DANGER_WORD, detail='its name adds "delete"'
)


def call(outcome: ModelCallOutcome, problem: str | None = None) -> ModelCall:
    return ModelCall(purpose=ModelCallPurpose.CHOOSE, outcome=outcome, problem=problem, usage=USAGE)


def rung3(
    outcome: RungOutcome,
    *,
    rejection: SafetyRejection | None = None,
    model: bool = True,
    **evidence: object,
) -> HealAttemptReport:
    values: dict[str, object] = {"prompt_version": "choose-candidate/1", "shown": SHOWN, **evidence}
    return HealAttemptReport(
        rung=3,
        attempt=1,
        outcome=outcome,
        candidates=(
            ScoredCandidate(
                id="c1",
                origin=CandidateOrigin.PAGE,
                identity=IdentityReport(tag="button", role="button", name="Quarterly download"),
                score=0.5,
                features=FEATURES,
                rejection=rejection,
            ),
        ),
        model=ModelChoiceEvidence.model_validate(values) if model else None,
    )


@pytest.mark.parametrize(
    ("reason", "report", "message"),
    [
        (
            AbstentionReason.MODEL_ABSTAINED,
            rung3(RungOutcome.MODEL_ABSTAINED, calls=(call(ModelCallOutcome.ANSWERED),)),
            "neither the scoring nor the model identified the recorded control: the model was "
            "shown 2 candidates and answered that none of them clearly is",
        ),
        (
            AbstentionReason.MODEL_CHOICE_OUT_OF_RANGE,
            rung3(RungOutcome.CHOICE_OUT_OF_RANGE, choice=7),
            "the model answered 7, which is not one of the 2 candidates it was shown, so nothing "
            "was chosen",
        ),
        (
            AbstentionReason.MODEL_OUTPUT_INVALID,
            rung3(
                RungOutcome.OUTPUT_INVALID,
                calls=(
                    call(ModelCallOutcome.INVALID_OUTPUT, "it was empty"),
                    call(ModelCallOutcome.INVALID_OUTPUT, "it was cut off before it ended"),
                ),
            ),
            "the model twice replied in a form that could not be used (it was cut off before it "
            "ended)",
        ),
        (
            AbstentionReason.MODEL_OUTPUT_INVALID,
            rung3(RungOutcome.OUTPUT_INVALID),
            "the model twice replied in a form that could not be used (no usable reply)",
        ),
        (
            AbstentionReason.MODEL_UNAVAILABLE,
            rung3(RungOutcome.MODEL_UNAVAILABLE, unavailable="the provider answered HTTP 503"),
            "the model could not be asked: the provider answered HTTP 503",
        ),
        (
            AbstentionReason.MODEL_UNAVAILABLE,
            rung3(RungOutcome.MODEL_UNAVAILABLE, model=False),
            "the model could not be asked: the provider did not answer",
        ),
        (
            AbstentionReason.MODEL_BUDGET_EXHAUSTED,
            rung3(
                RungOutcome.BUDGET_EXHAUSTED,
                budget=BudgetStop(
                    scope=BudgetScope.RUN, limit=4, detail="the run's calls are used"
                ),
            ),
            "no model call was made: the run's calls are used",
        ),
        (
            AbstentionReason.MODEL_BUDGET_EXHAUSTED,
            rung3(RungOutcome.BUDGET_EXHAUSTED),
            "no model call was made: the budget is used up",
        ),
        (
            AbstentionReason.MODEL_CHOICE_REFUSED,
            rung3(RungOutcome.CHOICE_REFUSED, rejection=REFUSED, choice=1),
            'the model chose button "Quarterly download", which was refused: its name adds '
            '"delete"',
        ),
        (
            AbstentionReason.MODEL_CHOICE_REFUSED,
            rung3(RungOutcome.CHOICE_REFUSED),
            "the model's choice was refused by a safety rule",
        ),
        (
            AbstentionReason.PAGE_NEVER_STABLE,
            rung3(RungOutcome.PAGE_NEVER_STABLE),
            "the page changed while the model was choosing, so its choice was not used",
        ),
        (
            AbstentionReason.UNVERIFIABLE,
            rung3(RungOutcome.NOT_ASKED),
            "the scoring could not decide, and a model was not asked because this step has no "
            "checkpoint that could prove its choice",
        ),
        (
            AbstentionReason.AUTHENTICATION_LIMIT,
            rung3(RungOutcome.NOT_ASKED),
            "the scoring could not decide, and a model was not asked because this sign-in step "
            "has used its one heal attempt",
        ),
        (
            AbstentionReason.ATTEMPTS_EXHAUSTED,
            rung3(RungOutcome.NOT_ASKED),
            "the scoring could not decide, and a model was not asked because this step has used "
            "every heal attempt it may make",
        ),
        (
            AbstentionReason.RESTORE_FAILED,
            rung3(RungOutcome.MODEL_ABSTAINED),
            "the heal ladder abstained (restore_failed)",
        ),
    ],
)
def test_every_rung3_abstention_says_what_happened(
    reason: AbstentionReason, report: HealAttemptReport, message: str
) -> None:
    assert abstention_message(reason, report, CONFIG) == message


def test_the_context_carries_what_the_model_was_shown_answered_and_why_it_stopped() -> None:
    resets = datetime(2026, 9, 14, tzinfo=UTC)
    report = rung3(
        RungOutcome.CHOICE_REFUSED,
        rejection=REFUSED,
        calls=(
            call(ModelCallOutcome.INVALID_OUTPUT, "it was empty"),
            call(ModelCallOutcome.ANSWERED),
        ),
        choice=1,
        reason="It is the renamed export.",
        unavailable="paused",
        budget=BudgetStop(scope=BudgetScope.DAY, limit=200, resets_at=resets, detail="used up"),
    )

    error = abstained(AbstentionReason.MODEL_CHOICE_REFUSED, report, CONFIG)

    context = abstention_context(AbstentionReason.MODEL_CHOICE_REFUSED, report, CONFIG)
    assert error.context == context
    assert {key: context[key] for key in context if key.startswith(("model_", "budget_"))} == {
        "model_shown": 2,
        "model_calls": 2,
        "model_provider": "ollama",
        "model_name": "local-chooser:4b",
        "model_choice": 1,
        "model_reason": "It is the renamed export.",
        "model_problem": "it was empty",
        "model_unavailable": "paused",
        "budget_scope": "day",
        "budget_limit": 200,
        "budget_resets_at": "2026-09-14T00:00:00+00:00",
    }
    assert (context["reason"], context["rung"], context["rejection"]) == (
        "model_choice_refused",
        3,
        "danger_word",
    )


def test_a_report_without_model_evidence_carries_no_model_facts() -> None:
    report = rung3(RungOutcome.MODEL_UNAVAILABLE, model=False)

    context = abstention_context(AbstentionReason.MODEL_UNAVAILABLE, report, CONFIG)

    assert not [key for key in context if key.startswith(("model_", "budget_"))]
