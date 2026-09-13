"""Invariants of Rung 2, on generated fingerprints and candidates from made-up applications.

- An unchanged element always scores at least as well as any other candidate.
- A candidate that gains a danger word is refused, and is never the accepted heal.
- Ranking and acceptance do not depend on the order the page listed candidates in.
- Of two candidates that differ only in name, the closer name scores higher.
- For every configuration Settings accepts: a candidate with no wording or identity attributes
  in common never reaches the threshold, and one weak clue never opens the margin.
"""

import math

from hypothesis import assume, given
from hypothesis import strategies as st

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import (
    CandidateOrigin,
    FeatureScores,
    HealAttemptReport,
    RejectionReason,
    RungOutcome,
)
from mendwork.engine.domain.steps import Step
from mendwork.engine.healing.acceptance import Accepted, decide
from mendwork.engine.healing.checks import safety_rejection
from mendwork.engine.healing.config import FeatureWeights, acceptance_problems
from mendwork.engine.healing.context import scored_candidate
from mendwork.engine.healing.features import text_similarity
from mendwork.engine.healing.scoring import ScoredElement, rank, score_candidate, weighted_score
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.ports.element_types import Box
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.unit.healing.builders import live, step
from tests.unit.recording.builders import VOCABULARY
from tests.unit.replay.builders import healing

CONFIG = healing()
WORDS = (
    "ledger", "invoice", "customer", "reference", "quarterly", "monthly", "vendor", "payroll",
    "expense", "budget", "summary", "statement", "account", "billing", "export", "print",
)  # fmt: skip
DANGER = sorted(VOCABULARY.danger_words - VOCABULARY.soft_verbs)
LEVELS = ("main", "section", "form", "div", "article", "nav", "ul", "li", "header")
KINDS = (
    ("button", "button", "button"),
    ("a", "link", None),
    ("input", "textbox", "text"),
    ("input", None, "date"),
)
WEAK = ("nearby_text", "structural_path", "position")


def levels() -> st.SearchStrategy[list[str]]:
    return st.lists(st.sampled_from(LEVELS), min_size=1, max_size=4)


def names() -> st.SearchStrategy[str]:
    return st.lists(st.sampled_from(WORDS), min_size=1, max_size=3).map(" ".join)


def boxes() -> st.SearchStrategy[dict[str, float]]:
    return st.tuples(st.floats(0, 0.8), st.floats(0, 0.9), st.floats(0.01, 0.2)).map(
        lambda values: {"x": values[0], "y": values[1], "width": values[2], "height": 0.05}
    )


@st.composite
def fingerprints(draw: st.DrawFn) -> Fingerprint:
    tag, role, kind = draw(st.sampled_from(KINDS))
    name = draw(names())
    identifier = draw(st.sampled_from(WORDS))
    attributes: dict[str, object] = {"type": kind} if kind else {}
    if draw(st.booleans()):
        attributes["id"] = f"{identifier}-control"
    if draw(st.booleans()):
        attributes["data_testid"] = f"{identifier}-test"
    if tag == "a":
        attributes["href"] = f"/{identifier}"
    return Fingerprint.model_validate(
        {
            "tag": tag,
            "role": role,
            "accessible_name": name,
            "text": None if tag == "input" else name,
            "label_text": name if tag == "input" else None,
            "attributes": attributes,
            "nearby_text": draw(st.lists(names(), max_size=2, unique=True)),
            "structural_path": " > ".join([*draw(levels()), tag]),
            "bbox": draw(st.one_of(st.none(), boxes())),
            "selectors": [{"strategy": "css", "value": f"#{identifier}"}],
        }
    )  # fmt: skip


@st.composite
def variants(draw: st.DrawFn, fingerprint: Fingerprint, ref: str) -> LiveCandidate:
    identity: dict[str, object] = {}
    facts: dict[str, object] = {}
    if draw(st.booleans()):
        name = draw(names())
        identity["name"] = name
        facts["text"] = name
        facts["label_text"] = name
    if draw(st.booleans()):
        facts["id"] = draw(st.sampled_from([None, "other-control"]))
        facts["data_testid"] = draw(st.sampled_from([None, "other-test"]))
    if draw(st.booleans()):
        tag, role, kind = draw(st.sampled_from(KINDS))
        identity.update({"tag": tag, "role": role, "input_type": kind})
    if draw(st.booleans()):
        facts["nearby_text"] = tuple(draw(st.lists(names(), max_size=2)))
    if draw(st.booleans()):
        facts["structural_path"] = " > ".join(
            draw(st.lists(st.sampled_from(LEVELS), min_size=1, max_size=5))
        )
    if draw(st.booleans()):
        facts["box"] = Box(**draw(boxes()))
    return live(fingerprint, ref, identity=identity, facts=facts)  # fmt: skip


def pools(fingerprint: Fingerprint) -> st.SearchStrategy[list[LiveCandidate]]:
    return st.integers(1, 6).flatmap(
        lambda size: st.tuples(*(variants(fingerprint, f"e{index}") for index in range(size))).map(
            list
        )
    )


def step_for(fingerprint: Fingerprint) -> Step:
    checks = [{"kind": "text_present", "text": "Done"}]
    target = fingerprint.model_dump(mode="json", exclude_none=True)
    if fingerprint.tag == "input":
        return step(
            {"id": "fill_it", "intent": "Fill it", "action": "fill", "risk": "caution",
             "target": target, "value": {"kind": "literal", "value": "2026-01-02"},
             "checkpoints": [{"kind": "field_has_value"}]}
        )  # fmt: skip
    return step(
        {"id": "click_it", "intent": "Click it", "action": "click", "risk": "safe",
         "target": target, "checkpoints": checks}
    )  # fmt: skip


def score_all(fingerprint: Fingerprint, pool: list[LiveCandidate]) -> list[ScoredElement]:
    action = step_for(fingerprint)
    return [
        score_candidate(
            fingerprint,
            candidate,
            CandidateOrigin.PAGE,
            CONFIG,
            safety_rejection(action, fingerprint, candidate, VOCABULARY),
        )
        for candidate in pool
    ]


@given(st.data())
def test_an_unchanged_element_scores_at_least_as_well_as_any_other_candidate(
    data: st.DataObject,
) -> None:
    fingerprint = data.draw(fingerprints())
    pool = data.draw(pools(fingerprint))
    unchanged = live(fingerprint, "unchanged")

    scores = [item.score for item in score_all(fingerprint, [unchanged, *pool])]

    assert scores[0] == max(scores)


@given(st.data())
def test_a_candidate_that_gains_a_danger_word_is_refused_and_never_accepted(
    data: st.DataObject,
) -> None:
    fingerprint = data.draw(fingerprints())
    pool = [live(fingerprint, "unchanged"), *data.draw(pools(fingerprint))]
    index = data.draw(st.integers(0, len(pool) - 1))
    word = data.draw(st.sampled_from(DANGER))
    chosen = pool[index]
    renamed = chosen.identity.model_copy(update={"name": f"{chosen.identity.name} {word}"})
    dangerous = chosen.model_copy(update={"identity": renamed})
    pool[index] = dangerous

    items = score_all(fingerprint, pool)
    decision = decide(
        rank(items), threshold=CONFIG.accept_threshold, required_margin=CONFIG.accept_margin
    )

    refused = items[index].rejection
    assert refused is not None
    assert refused.reason is RejectionReason.DANGER_WORD
    if isinstance(decision, Accepted):
        assert decision.winner.candidate.element != dangerous.element
    if rank(items)[0].candidate.element == dangerous.element:
        assert not isinstance(decision, Accepted)
        assert decision.outcome is RungOutcome.TOP_REJECTED  # fmt: skip


@given(st.data())
def test_ranking_and_acceptance_ignore_the_order_candidates_were_listed_in(
    data: st.DataObject,
) -> None:
    fingerprint = data.draw(fingerprints())
    pool = [live(fingerprint, "unchanged"), *data.draw(pools(fingerprint))]
    shuffled = data.draw(st.permutations(pool))

    first = rank(score_all(fingerprint, pool))
    second = rank(score_all(fingerprint, list(shuffled)))
    one = decide(first, threshold=CONFIG.accept_threshold, required_margin=CONFIG.accept_margin)
    two = decide(second, threshold=CONFIG.accept_threshold, required_margin=CONFIG.accept_margin)

    assert [item.signature for item in first] == [item.signature for item in second]
    assert [item.score for item in first] == [item.score for item in second]
    assert type(one) is type(two)
    if isinstance(one, Accepted) and isinstance(two, Accepted):
        assert one.winner.signature == two.winner.signature
    else:
        assert one == two or one.outcome == two.outcome  # type: ignore[union-attr] # both Declined


@given(st.data())
def test_of_two_candidates_differing_only_in_name_the_closer_name_scores_higher(
    data: st.DataObject,
) -> None:
    fingerprint = data.draw(fingerprints())
    recorded = fingerprint.accessible_name or ""
    first_extra = data.draw(st.lists(st.sampled_from(WORDS), max_size=1))
    second_extra = data.draw(st.lists(st.sampled_from(WORDS), min_size=2, max_size=4))
    first_name = " ".join([recorded, *first_extra])
    second_name = " ".join([recorded, *second_extra])
    floor = CONFIG.name_similarity_floor
    first_similarity = text_similarity(fingerprint.accessible_name, first_name, floor)
    second_similarity = text_similarity(fingerprint.accessible_name, second_name, floor)
    assume(first_similarity != second_similarity)

    first, second = score_all(
        fingerprint,
        [
            live(fingerprint, "a", identity={"name": first_name}, facts={"text": first_name}),
            live(fingerprint, "b", identity={"name": second_name}, facts={"text": second_name}),
        ],
    )

    assert (first.score > second.score) == (first_similarity > second_similarity)


@st.composite
def safe_configurations(draw: st.DrawFn) -> tuple[FeatureWeights, float, float]:
    """Weights, threshold, and margin that Settings would accept."""
    threshold = draw(st.floats(0.2, 1.0))
    context_total = draw(st.floats(0.0, 0.99)) * threshold
    context = _split(draw(st.lists(st.floats(0.0, 1.0), min_size=5, max_size=5)), context_total)
    anchors = _split(
        draw(st.lists(st.floats(0.01, 1.0), min_size=3, max_size=3)), 1 - context_total
    )
    weights = FeatureWeights(
        name=anchors[0],
        label=anchors[1],
        attributes=anchors[2],
        role=context[0],
        tag_type=context[1],
        nearby_text=context[2],
        structural_path=context[3],
        position=context[4],
    )
    weakest = max(context[2], context[3], context[4])
    assume(weakest < 0.99)
    margin = draw(st.floats(weakest + 0.005, 0.995))
    assume(not acceptance_problems(weights.by_feature(), threshold, margin))
    return weights, threshold, margin


def _split(parts: list[float], total: float) -> list[float]:
    whole = math.fsum(parts)
    if whole == 0:
        return [total / len(parts)] * len(parts)
    return [part / whole * total for part in parts]


def feature_values() -> st.SearchStrategy[float]:
    return st.floats(0.0, 1.0)


@given(safe_configurations(), st.lists(feature_values(), min_size=5, max_size=5))
def test_no_accepted_configuration_lets_context_alone_reach_the_threshold(
    configuration: tuple[FeatureWeights, float, float], values: list[float]
) -> None:
    weights, threshold, _ = configuration
    role, tag_type, nearby, path, position = values
    features = FeatureScores(
        name=0, label=0, attributes=0, role=role, tag_type=tag_type,
        nearby_text=nearby, structural_path=path, position=position,
    )  # fmt: skip

    assert weighted_score(features, weights) < threshold


@given(
    safe_configurations(),
    st.lists(feature_values(), min_size=8, max_size=8),
    st.sampled_from(WEAK),
    feature_values(),
)
def test_no_accepted_configuration_lets_one_weak_clue_open_the_margin(
    configuration: tuple[FeatureWeights, float, float],
    values: list[float],
    weak: str,
    changed: float,
) -> None:
    weights, _, margin = configuration
    names_in_order = ("name", "label", "attributes", "role", "tag_type", *WEAK)
    features = FeatureScores.model_validate(dict(zip(names_in_order, values, strict=True)))
    moved = features.model_copy(update={weak: changed})

    assert abs(weighted_score(features, weights) - weighted_score(moved, weights)) < margin


@given(st.data())
def test_heal_reports_survive_a_json_round_trip(data: st.DataObject) -> None:
    fingerprint = data.draw(fingerprints())
    ranked = rank(score_all(fingerprint, data.draw(pools(fingerprint))))
    report = HealAttemptReport(
        rung=2,
        attempt=1,
        outcome=RungOutcome.BELOW_THRESHOLD,
        candidates=tuple(
            scored_candidate(item, f"c{position}", SecretScrubber())
            for position, item in enumerate(ranked, start=1)
        ),
        considered=len(ranked),
        score=ranked[0].score,
    )  # fmt: skip

    assert HealAttemptReport.model_validate_json(report.model_dump_json()) == report
