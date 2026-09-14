"""Which Rung 2 declines Rung 3 takes up, and which candidates a model may be shown.

- **Outcomes.** Only a ranking whose closest candidate scored below the threshold or led by
  less than the margin goes to the model. A refused top candidate means a person should look
  (ADR 0009); a capped or unstable page cannot be compared; no candidates leaves nothing to
  choose from.
- **Eligible candidates** passed every safety rule and share wording (name or label) or
  identity attributes with the recording. A refused candidate is proven not to be the target,
  and one sharing only context cannot establish identity (invariant B), for a model as for
  scoring.
- **Look-alikes.** Two candidates with identical descriptions can only be told apart by
  position, so no choice between them means anything: the model is not asked when the best
  eligible candidate has a twin, and a pick with a twin anywhere among the eligible candidates
  (shown or not) is refused.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from mendwork.engine.domain.heals import FeatureScores, RungOutcome
from mendwork.engine.domain.model_evidence import CandidateDescription
from mendwork.engine.healing.scoring import ScoredElement

RUNG3_OUTCOMES: Final = frozenset({RungOutcome.BELOW_THRESHOLD, RungOutcome.BELOW_MARGIN})


@dataclass(frozen=True, slots=True)
class Eligible:
    """A candidate a model may be shown, with its id in Rung 2's ranking."""

    item: ScoredElement
    candidate_id: str
    description: CandidateDescription


@dataclass(frozen=True, slots=True)
class Eligibility:
    """The eligible candidates in ranking order, and how many others there were."""

    eligible: tuple[Eligible, ...]
    ineligible: int


def takes_up(outcome: RungOutcome) -> bool:
    """Whether Rung 3 runs after Rung 2 declined with this outcome."""
    return outcome in RUNG3_OUTCOMES


def shares_identity(features: FeatureScores) -> bool:
    """Whether a candidate has wording or identity attributes in common with the recording."""
    return features.name > 0 or features.label > 0 or features.attributes > 0


def eligibility(
    ranked: Sequence[ScoredElement],
    describe: Callable[[ScoredElement], CandidateDescription],
) -> Eligibility:
    """The candidates of a ranking a model may be shown, keeping their ranking ids."""
    eligible = tuple(
        Eligible(item, f"c{position}", describe(item))
        for position, item in enumerate(ranked, start=1)
        if item.rejection is None and shares_identity(item.features)
    )
    return Eligibility(eligible, len(ranked) - len(eligible))


def has_look_alike(candidate: Eligible, eligible: Sequence[Eligible]) -> bool:
    """Whether another eligible candidate reads exactly like this one."""
    return any(
        other.candidate_id != candidate.candidate_id and other.description == candidate.description
        for other in eligible
    )
