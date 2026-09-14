"""Which declines reach Rung 3, and which candidates a model may be shown."""

import pytest

from mendwork.engine.domain.heals import (
    CandidateOrigin,
    FeatureScores,
    RejectionReason,
    RungOutcome,
    SafetyRejection,
)
from mendwork.engine.domain.model_evidence import CandidateDescription
from mendwork.engine.healing.eligibility import (
    RUNG3_OUTCOMES,
    Eligible,
    eligibility,
    has_look_alike,
    shares_identity,
    takes_up,
)
from mendwork.engine.healing.scoring import ScoredElement
from tests.unit.healing.builders import export_button, live

REFUSED = SafetyRejection(reason=RejectionReason.DANGER_WORD, detail='its name adds "delete"')


def features(**scores: float) -> FeatureScores:
    values = dict.fromkeys(FeatureScores.model_fields, 0.0)
    return FeatureScores.model_validate({**values, **scores})


def item(
    key: str,
    *,
    called: str = "Export ledger",
    rejection: SafetyRejection | None = None,
    **scores: float,
) -> ScoredElement:
    return ScoredElement(
        candidate=live(export_button(), key, identity={"name": called}),
        origin=CandidateOrigin.PAGE,
        features=features(**scores),
        score=0.5,
        signature=(key,),
        rejection=rejection,
    )


def described(element: ScoredElement) -> CandidateDescription:
    return CandidateDescription(kind="button", name=element.candidate.identity.name)


@pytest.mark.parametrize("outcome", list(RungOutcome))
def test_only_a_ranking_below_the_threshold_or_margin_goes_to_a_model(
    outcome: RungOutcome,
) -> None:
    assert takes_up(outcome) is (outcome in {RungOutcome.BELOW_THRESHOLD, RungOutcome.BELOW_MARGIN})
    assert frozenset({RungOutcome.BELOW_THRESHOLD, RungOutcome.BELOW_MARGIN}) == RUNG3_OUTCOMES


@pytest.mark.parametrize("feature", ["name", "label", "attributes"])
def test_wording_or_identity_attributes_in_common_make_a_candidate_eligible(feature: str) -> None:
    assert shares_identity(features(**{feature: 0.01}))


def test_context_alone_never_makes_a_candidate_eligible() -> None:
    context = features(role=1, tag_type=1, nearby_text=1, structural_path=1, position=1)

    assert not shares_identity(context)


def test_refused_and_context_only_candidates_are_never_shown_and_ids_keep_their_rank() -> None:
    ranked = (
        item("a", name=0.4),
        item("b", attributes=1.0, rejection=REFUSED),
        item("c", role=1, position=1),
        item("d", label=0.2),
    )

    judged = eligibility(ranked, described)

    assert [(eligible.candidate_id, eligible.item.signature) for eligible in judged.eligible] == [
        ("c1", ("a",)),
        ("c4", ("d",)),
    ]
    assert judged.ineligible == 2


def test_candidates_that_read_the_same_are_look_alikes_and_differences_in_any_text_are_not() -> (
    None
):
    same = CandidateDescription(kind="button", name="View", nearby_text=("Order 17",))
    first = Eligible(item("a", name=1), "c1", same)
    twin = Eligible(item("b", name=1), "c2", same)
    other_row = Eligible(
        item("c", name=1), "c3", same.model_copy(update={"nearby_text": ("Order 18",)})
    )

    assert has_look_alike(first, [first, twin, other_row])
    assert not has_look_alike(other_row, [first, twin, other_row])
    assert not has_look_alike(first, [first])
