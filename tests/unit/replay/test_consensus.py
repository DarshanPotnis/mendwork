"""Rung 0 consensus: agreement, disagreement, ambiguity, and not found, from hits and misses."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.domain.targets import SelectorOutcome
from mendwork.engine.ports.browser_types import ElementRef, UniqueMatch
from mendwork.engine.replay.consensus import (
    Agreement,
    Disagreement,
    NoMatch,
    SeveralMatches,
    decide,
    number_elements,
    selector_outcome,
    selector_reports,
)
from tests.unit.replay.builders import CSS, ROLE_NAME, TEST_ID

HIT, NONE, MANY = SelectorOutcome.HIT, SelectorOutcome.NONE, SelectorOutcome.MANY


def test_hits_on_one_element_agree_on_the_best_ranked_hit() -> None:
    assert decide([NONE, HIT, HIT], [0, 0]) == Agreement(rank=1, ranks=(1, 2))


def test_hits_on_different_elements_disagree_and_never_pick_one() -> None:
    assert decide([HIT, HIT, HIT], [0, 1, 0]) == Disagreement(groups=((0, 2), (1,)))


def test_no_hit_but_several_matches_is_ambiguous() -> None:
    assert decide([NONE, MANY, NONE, MANY], []) == SeveralMatches(ranks=(1, 3))


def test_a_hit_is_decisive_even_when_another_selector_matched_several() -> None:
    assert decide([MANY, HIT], [0]) == Agreement(rank=1, ranks=(1,))


def test_nothing_matching_is_not_found() -> None:
    assert decide([NONE, NONE], []) == NoMatch()


def test_element_numbers_must_line_up_with_the_hits() -> None:
    with pytest.raises(ValueError, match="one entry per hit"):
        decide([HIT, HIT], [0])


@pytest.mark.parametrize(
    ("match", "outcome"),
    [
        (UniqueMatch(level_counts=(1,), element=ElementRef("e1")), HIT),
        (UniqueMatch(level_counts=(0,)), NONE),
        (UniqueMatch(level_counts=(2,)), MANY),
        (UniqueMatch(level_counts=(1, 3)), MANY),
        (UniqueMatch(level_counts=(3,)), MANY),
        (UniqueMatch(level_counts=(1, 0)), NONE),
        (UniqueMatch(level_counts=()), NONE),
    ],
)
def test_a_selector_outcome_is_read_from_its_last_counted_level(
    match: UniqueMatch, outcome: SelectorOutcome
) -> None:
    assert selector_outcome(match) is outcome


def test_elements_are_numbered_in_order_of_first_appearance() -> None:
    assert number_elements([7, 3, 7, 1]) == (0, 1, 0, 2)


def test_reports_carry_counts_outcomes_and_element_numbers() -> None:
    matches = [
        UniqueMatch(level_counts=(0,)),
        UniqueMatch(level_counts=(1,), element=ElementRef("e1")),
        UniqueMatch(level_counts=(2,)),
    ]

    reports = selector_reports((TEST_ID, ROLE_NAME, CSS), matches, [0])

    assert [(r.rank, r.strategy, r.outcome, r.element) for r in reports] == [
        (0, "test_id", NONE, None),
        (1, "role_name", HIT, 0),
        (2, "css", MANY, None),
    ]


outcome_lists = st.lists(st.sampled_from(list(SelectorOutcome)), min_size=1, max_size=10)


@given(outcome_lists)
def test_when_all_hits_agree_the_winner_is_the_best_ranked_hit(
    outcomes: list[SelectorOutcome],
) -> None:
    hits = [rank for rank, outcome in enumerate(outcomes) if outcome is HIT]
    verdict = decide(outcomes, [0] * len(hits))

    if hits:
        assert verdict == Agreement(rank=hits[0], ranks=tuple(hits))
    else:
        assert isinstance(verdict, SeveralMatches | NoMatch)


@given(
    st.lists(
        st.tuples(st.sampled_from(list(SelectorOutcome)), st.integers(0, 2)), min_size=1, max_size=8
    ),
    st.randoms(use_true_random=False),
)
def test_the_kind_of_verdict_does_not_depend_on_selector_order(
    entries: list[tuple[SelectorOutcome, int]],
    random: "st.random.Random",  # type: ignore[name-defined]
) -> None:
    def verdict_kind(ordered: list[tuple[SelectorOutcome, int]]) -> type:
        outcomes = [outcome for outcome, _ in ordered]
        elements = number_elements([element for outcome, element in ordered if outcome is HIT])
        return type(decide(outcomes, elements))

    shuffled = list(entries)
    random.shuffle(shuffled)

    assert verdict_kind(entries) is verdict_kind(shuffled)
