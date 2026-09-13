"""Abstention messages and the numbers behind them, for every reason the ladder can stop."""

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
from mendwork.engine.domain.targets import IdentityReport
from mendwork.engine.healing.explain import abstention_context, abstention_message
from tests.unit.replay.builders import healing

CONFIG = healing()
FEATURES = FeatureScores(
    name=0, label=0, attributes=1, role=1, tag_type=1, nearby_text=1, structural_path=1, position=1
)
REFUSED = SafetyRejection(
    reason=RejectionReason.DANGER_WORD,
    detail='its name adds "delete", which the recorded name does not have',
)


def report(**fields: object) -> HealAttemptReport:
    values: dict[str, object] = {
        "rung": 2,
        "attempt": 1,
        "outcome": RungOutcome.BELOW_THRESHOLD,
        "considered": 3,
        "on_page": 4,
        "score": 0.52,
        "margin": 0.08,
    }
    return HealAttemptReport.model_validate({**values, **fields})


def refused_top() -> HealAttemptReport:
    top = ScoredCandidate(
        id="c1",
        origin=CandidateOrigin.RUNG0_DRIFTED,
        identity=IdentityReport(tag="button", role="button", name="Delete ledger"),
        score=0.7,
        features=FEATURES,
        rejection=REFUSED,
    )
    return report(outcome=RungOutcome.TOP_REJECTED, candidates=[top])


MESSAGES = [
    (
        AbstentionReason.NO_CANDIDATES,
        report(),
        "no element on the page could receive this step's action",
    ),
    (
        AbstentionReason.BELOW_THRESHOLD,
        report(),
        "the closest candidate scored 0.52, below the accept threshold of 0.60",
    ),
    (
        AbstentionReason.BELOW_MARGIN,
        report(score=0.9),
        "the closest candidate scored 0.90 but led the next by only 0.08, less than the required "
        "margin of 0.15, so choosing would be a guess",
    ),
    (
        AbstentionReason.TOP_REJECTED,
        refused_top(),
        'the element most like the recorded one was refused: its name adds "delete", which the '
        "recorded name does not have",
    ),
    (
        AbstentionReason.TOP_REJECTED,
        report(),
        "the element most like the recorded one was refused: a safety rule refused it",
    ),
    (
        AbstentionReason.CANDIDATE_CAP_REACHED,
        report(on_page=5120, score=None, margin=None),
        "the page has 5120 elements this action could receive, more than the limit of 4000, so "
        "healing does not run on it",
    ),
    (
        AbstentionReason.PAGE_NEVER_STABLE,
        report(),
        "the page kept changing while candidates were compared",
    ),
    (
        AbstentionReason.UNVERIFIABLE,
        report(score=None, margin=None),
        "the heal ladder abstained (unverifiable)",
    ),
]


@pytest.mark.parametrize(("reason", "attempt", "message"), MESSAGES)
def test_every_abstention_says_why_in_words(
    reason: AbstentionReason, attempt: HealAttemptReport, message: str
) -> None:
    assert abstention_message(reason, attempt, CONFIG) == message


def test_every_reason_the_ladder_can_give_has_a_message() -> None:
    for reason in AbstentionReason:
        assert abstention_message(reason, report(), CONFIG)


def test_the_context_carries_every_number_behind_the_decision() -> None:
    assert abstention_context(AbstentionReason.BELOW_MARGIN, report(), CONFIG) == {
        "reason": "below_margin",
        "rung": 2,
        "on_page": 4,
        "considered": 3,
        "score": 0.52,
        "margin": 0.08,
        "threshold": 0.6,
        "required_margin": 0.15,
        "candidates_max": 4000,
    }


def test_the_context_names_the_rule_that_refused_the_top_candidate() -> None:
    context = abstention_context(AbstentionReason.TOP_REJECTED, refused_top(), CONFIG)

    assert context["rejection"] == "danger_word"
