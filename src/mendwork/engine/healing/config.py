"""Healing configuration, handed to the engine by the app that builds it from Settings.

There are no defaults here: Settings is the only place a default may live. The acceptance
rule's safety invariants are checked here too, and Settings reuses the same check, so no
configuration can make a heal acceptable on context alone (ADR 0009):

- **Context never establishes identity.** Role, tag and type, nearby text, structural path,
  and position together must weigh less than the accept threshold, so a candidate that
  shares neither wording nor identity attributes with the recording can never be accepted.
- **Weak clues never separate look-alikes.** The margin must exceed every single position or
  context weight, so two controls differing only in where they sit are never told apart.
"""

import math
from collections.abc import Mapping
from typing import Final, Self

from pydantic import Field, model_validator

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.heals import FeatureName
from mendwork.engine.safety.risk import RiskVocabulary

_SUM_TOLERANCE: Final = 1e-9
CONTEXT_FEATURES: Final = (
    FeatureName.ROLE,
    FeatureName.TAG_TYPE,
    FeatureName.NEARBY_TEXT,
    FeatureName.STRUCTURAL_PATH,
    FeatureName.POSITION,
)
"""Features that describe where and what kind of element it is, never which one."""
WEAK_FEATURES: Final = (FeatureName.NEARBY_TEXT, FeatureName.STRUCTURAL_PATH, FeatureName.POSITION)
"""Features that change with layout alone."""


def acceptance_problems(
    weights: Mapping[FeatureName, float], threshold: float, margin: float
) -> tuple[str, ...]:
    """Every way a set of weights, threshold, and margin breaks the acceptance invariants."""
    problems: list[str] = []
    missing = sorted(set(FeatureName) - set(weights))
    if missing:
        return (f"heal feature weights are missing: {', '.join(missing)}",)
    total = math.fsum(weights.values())
    if abs(total - 1.0) > _SUM_TOLERANCE:
        problems.append(f"heal feature weights must sum to 1, but they sum to {total:.6g}")
    context = math.fsum(weights[name] for name in CONTEXT_FEATURES)
    if context >= threshold:
        problems.append(
            f"the role, tag/type, nearby-text, structural-path, and position weights sum to "
            f"{context:.6g}, which is not below the accept threshold {threshold:.6g}: a candidate "
            "sharing neither wording nor identity attributes with the recording could be accepted"
        )
    weakest = max(weights[name] for name in WEAK_FEATURES)
    if margin <= weakest:
        problems.append(
            f"the accept margin {margin:.6g} must be larger than every nearby-text, "
            f"structural-path, and position weight (the largest is {weakest:.6g}), or one weak "
            "clue could separate two look-alike controls"
        )
    return tuple(problems)


class FeatureWeights(DomainModel):
    """How much each feature contributes to a candidate's score; the weights sum to 1."""

    name: float = Field(ge=0.0, le=1.0)
    label: float = Field(ge=0.0, le=1.0)
    attributes: float = Field(ge=0.0, le=1.0)
    role: float = Field(ge=0.0, le=1.0)
    tag_type: float = Field(ge=0.0, le=1.0)
    nearby_text: float = Field(ge=0.0, le=1.0)
    structural_path: float = Field(ge=0.0, le=1.0)
    position: float = Field(ge=0.0, le=1.0)

    def by_feature(self) -> dict[FeatureName, float]:
        """The weights keyed by feature."""
        return {feature: getattr(self, feature.value) for feature in FeatureName}


class HealingConfig(DomainModel):
    """Every threshold, weight, and limit the heal ladder uses."""

    weights: FeatureWeights
    accept_threshold: float = Field(gt=0.0, le=1.0)
    accept_margin: float = Field(gt=0.0, lt=1.0)
    name_similarity_floor: float = Field(ge=0.0, lt=1.0)
    """Text similarity at or below this counts as nothing in common."""
    position_scale: float = Field(gt=0.0)
    """The distance, as a fraction of the document, at which position proximity reaches 0."""
    candidates_max: int = Field(ge=1)
    """A page with more action-compatible visible elements than this is never healed."""
    max_attempts: int = Field(ge=1)
    """How many healed targets one step may act on, counting those that failed verification."""
    authentication_max_attempts: int = Field(ge=0, le=1)
    report_candidates: int = Field(ge=1)
    """How many of the best candidates each heal attempt reports."""
    timeout_ms: int = Field(ge=1)
    """The time one step may spend healing, attempts and restores included."""
    vocabulary: RiskVocabulary
    """The risk classifier's vocabulary: the healer reads danger exactly as it does."""

    @model_validator(mode="after")
    def _acceptance_is_safe(self) -> Self:
        problems = acceptance_problems(
            self.weights.by_feature(), self.accept_threshold, self.accept_margin
        )
        if problems:
            raise ValueError("; ".join(problems))
        return self
