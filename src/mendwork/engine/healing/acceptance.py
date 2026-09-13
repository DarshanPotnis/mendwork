"""The accept rule: act only on a clear, safe winner; otherwise abstain.

A heal is accepted only when all of these hold:

1. the best-scoring candidate passes every safety rule: when the element most like the
   recorded one is refused (it now names a destructive action, say), a person should look;
2. its score reaches the threshold;
3. it beats the best other candidate no rule refused by at least the margin, which is what
   keeps the ladder from clicking one of two look-alike controls.

A refused candidate below the top is proven not to be the target, so it cannot narrow the
margin. Abstaining is a correct outcome, and every number behind it is reported.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from mendwork.engine.domain.heals import RungOutcome
from mendwork.engine.healing.scoring import SCORE_DECIMALS, ScoredElement


@dataclass(frozen=True, slots=True)
class Accepted:
    """A winner that may be acted on, pending the risk gate and verification."""

    winner: ScoredElement
    runner_up: ScoredElement | None
    margin: float


@dataclass(frozen=True, slots=True)
class Declined:
    """Nothing may be acted on, and why."""

    outcome: RungOutcome
    runner_up: ScoredElement | None = None
    margin: float | None = None


def runner_up(ranked: Sequence[ScoredElement]) -> ScoredElement | None:
    """The best candidate after the top that no safety rule refused."""
    return next((item for item in ranked[1:] if item.rejection is None), None)


def margin_over(top: ScoredElement, second: ScoredElement | None) -> float:
    """How far the top candidate leads; the whole score when nothing else survived."""
    return round(top.score - (second.score if second is not None else 0.0), SCORE_DECIMALS)


def decide(
    ranked: Sequence[ScoredElement], *, threshold: float, required_margin: float
) -> Accepted | Declined:
    """Accept the top of a ranking, or decline with the reason."""
    if not ranked:
        return Declined(RungOutcome.NO_CANDIDATES)
    top = ranked[0]
    second = runner_up(ranked)
    margin = margin_over(top, second)
    if top.rejection is not None:
        return Declined(RungOutcome.TOP_REJECTED, second, margin)
    if top.score < threshold:
        return Declined(RungOutcome.BELOW_THRESHOLD, second, margin)
    if margin < required_margin:
        return Declined(RungOutcome.BELOW_MARGIN, second, margin)
    return Accepted(top, second, margin)
