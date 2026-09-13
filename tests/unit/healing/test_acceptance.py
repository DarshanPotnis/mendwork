"""The accept rule: a safe winner above the threshold that leads by the margin, or abstain."""

import pytest

from mendwork.engine.domain.heals import (
    CandidateOrigin,
    FeatureScores,
    RejectionReason,
    RungOutcome,
    SafetyRejection,
)
from mendwork.engine.healing.acceptance import Accepted, Declined, decide, margin_over, runner_up
from mendwork.engine.healing.scoring import rank, score_candidate, weighted_score
from tests.unit.healing.builders import export_button, live, scored
from tests.unit.replay.builders import WEIGHTS, healing

REFUSED = SafetyRejection(reason=RejectionReason.DANGER_WORD, detail="its name adds danger")


def accept(*items: object) -> Accepted | Declined:
    return decide(rank(items), threshold=0.60, required_margin=0.15)  # type: ignore[arg-type] # ScoredElement by construction


def test_nothing_to_compare_abstains() -> None:
    assert decide((), threshold=0.6, required_margin=0.15) == Declined(RungOutcome.NO_CANDIDATES)


def test_a_clear_winner_above_the_threshold_is_accepted() -> None:
    decision = accept(scored(0.8, "a"), scored(0.4, "b"))

    assert isinstance(decision, Accepted)
    assert decision.winner.signature == ("a",)
    assert decision.margin == pytest.approx(0.4)


def test_a_winner_exactly_at_the_threshold_and_margin_is_accepted() -> None:
    decision = accept(scored(0.6, "a"), scored(0.45, "b"))

    assert isinstance(decision, Accepted)


def test_a_winner_below_the_threshold_abstains_whatever_its_lead() -> None:
    decision = accept(scored(0.59, "a"))

    assert isinstance(decision, Declined)
    assert decision.outcome is RungOutcome.BELOW_THRESHOLD


def test_two_look_alikes_closer_than_the_margin_abstain() -> None:
    decision = accept(scored(0.9, "a"), scored(0.76, "b"))

    assert isinstance(decision, Declined)
    assert decision.outcome is RungOutcome.BELOW_MARGIN
    assert decision.margin == pytest.approx(0.14)
    assert decision.runner_up is not None


def test_a_refused_top_candidate_stops_the_ladder_even_with_a_safe_one_behind_it() -> None:
    decision = accept(scored(0.9, "a", rejection=REFUSED), scored(0.7, "b"))

    assert isinstance(decision, Declined)
    assert decision.outcome is RungOutcome.TOP_REJECTED


def test_a_refused_candidate_below_the_top_does_not_narrow_the_margin() -> None:
    decision = accept(scored(0.9, "a"), scored(0.85, "b", rejection=REFUSED), scored(0.3, "c"))

    assert isinstance(decision, Accepted)
    assert decision.runner_up is not None
    assert decision.runner_up.signature == ("c",)


def test_a_lone_winner_leads_by_its_whole_score() -> None:
    top = scored(0.7)

    assert runner_up((top,)) is None
    assert margin_over(top, None) == 0.7


def test_ranking_orders_by_score_then_signature_never_by_input_order() -> None:
    items = [scored(0.5, "b"), scored(0.9, "z"), scored(0.5, "a")]

    assert [item.signature for item in rank(items)] == [("z",), ("a",), ("b",)]
    assert rank(reversed(items)) == rank(items)


def test_weighted_scores_stay_within_zero_and_one() -> None:
    ones = FeatureScores(
        name=1,
        label=1,
        attributes=1,
        role=1,
        tag_type=1,
        nearby_text=1,
        structural_path=1,
        position=1,
    )

    assert weighted_score(ones, WEIGHTS) == 1.0
    heavy = WEIGHTS.model_copy(update=dict.fromkeys(WEIGHTS.model_dump(), 1.0))
    assert weighted_score(ones, heavy) == 1.0


def test_scoring_a_candidate_records_its_features_origin_and_signature() -> None:
    recorded = export_button()

    item = score_candidate(recorded, live(recorded), CandidateOrigin.RUNG0_DRIFTED, healing())

    assert item.score == 1.0
    assert item.origin is CandidateOrigin.RUNG0_DRIFTED
    assert item.signature[0] == "button"
    assert item.rejected(REFUSED).rejection == REFUSED
