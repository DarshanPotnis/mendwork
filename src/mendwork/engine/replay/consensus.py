"""Rung 0 consensus: what the recorded selectors, taken together, say about the target.

A selector *hits* when every scope level and the final locator each match exactly one
visible element. The verdict is a pure function of the hits and misses:

- every hit is the same element: that element, found by the best-ranked hit;
- hits point at different elements: ambiguous, because picking one would be a guess;
- no hits, but some selector matched several elements: ambiguous;
- no hits and nothing matched several: not found.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from mendwork.engine.domain.runs import SelectorOutcome, SelectorReport
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.ports.browser_types import UniqueMatch


@dataclass(frozen=True, slots=True)
class Agreement:
    """Every hit found the same element."""

    rank: int
    """The best-ranked selector that hit."""
    ranks: tuple[int, ...]
    """Every selector that hit, best first."""


@dataclass(frozen=True, slots=True)
class Disagreement:
    """Hits found different elements."""

    groups: tuple[tuple[int, ...], ...]
    """The ranks that found each distinct element, in order of first hit."""


@dataclass(frozen=True, slots=True)
class SeveralMatches:
    """Nothing hit, and these selectors matched more than one element at some level."""

    ranks: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class NoMatch:
    """No selector matched anything at some level."""


Verdict = Agreement | Disagreement | SeveralMatches | NoMatch


def selector_outcome(match: UniqueMatch) -> SelectorOutcome:
    """Classify one selector's result by its last counted level."""
    if match.element is not None:
        return SelectorOutcome.HIT
    if match.level_counts and match.level_counts[-1] > 1:
        return SelectorOutcome.MANY
    return SelectorOutcome.NONE


def number_elements(canonical: Sequence[int]) -> tuple[int, ...]:
    """Renumber canonical element keys 0, 1, 2… in order of first appearance."""
    numbers: dict[int, int] = {}
    return tuple(numbers.setdefault(key, len(numbers)) for key in canonical)


def decide(outcomes: Sequence[SelectorOutcome], hit_elements: Sequence[int]) -> Verdict:
    """The verdict for selectors in rank order.

    ``hit_elements`` numbers the element each hit found, one entry per hit in rank order,
    as returned by ``number_elements``.
    """
    hit_ranks = [rank for rank, outcome in enumerate(outcomes) if outcome is SelectorOutcome.HIT]
    if len(hit_ranks) != len(hit_elements):
        raise ValueError("hit_elements must have exactly one entry per hit")
    if hit_ranks:
        distinct = sorted(set(hit_elements))
        if len(distinct) == 1:
            return Agreement(rank=hit_ranks[0], ranks=tuple(hit_ranks))
        return Disagreement(
            groups=tuple(
                tuple(
                    rank
                    for rank, element in zip(hit_ranks, hit_elements, strict=True)
                    if element == group
                )
                for group in distinct
            )
        )
    several = tuple(
        rank for rank, outcome in enumerate(outcomes) if outcome is SelectorOutcome.MANY
    )
    return SeveralMatches(ranks=several) if several else NoMatch()


def selector_reports(
    selectors: Sequence[Selector], matches: Sequence[UniqueMatch], hit_elements: Sequence[int]
) -> tuple[SelectorReport, ...]:
    """Evidence for every selector, with each hit's element number."""
    remaining = iter(hit_elements)
    reports: list[SelectorReport] = []
    for rank, (selector, match) in enumerate(zip(selectors, matches, strict=True)):
        outcome = selector_outcome(match)
        reports.append(
            SelectorReport(
                rank=rank,
                strategy=selector.strategy,
                level_counts=match.level_counts,
                outcome=outcome,
                element=next(remaining) if outcome is SelectorOutcome.HIT else None,
            )
        )
    return tuple(reports)
