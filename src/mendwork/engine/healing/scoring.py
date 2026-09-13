"""Rung 2's score: a weighted sum of features, and a ranking that ignores input order.

Scores are rounded to nine decimals, so the same candidates always produce exactly the same
numbers and comparisons against the threshold and margin never hinge on float noise. Ties
are broken by each candidate's signature, never by the order the page listed them in.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import (
    CandidateOrigin,
    FeatureName,
    FeatureScores,
    SafetyRejection,
)
from mendwork.engine.healing.candidates import CandidateSignature, candidate_signature
from mendwork.engine.healing.config import FeatureWeights, HealingConfig
from mendwork.engine.healing.features import feature_scores
from mendwork.engine.ports.candidate_types import LiveCandidate

SCORE_DECIMALS: Final = 9


@dataclass(frozen=True, slots=True)
class ScoredElement:
    """A candidate with its features, score, signature, and any safety rejection."""

    candidate: LiveCandidate
    origin: CandidateOrigin
    features: FeatureScores
    score: float
    signature: CandidateSignature
    rejection: SafetyRejection | None = None

    def rejected(self, rejection: SafetyRejection) -> "ScoredElement":
        """The same candidate, refused by a safety rule."""
        return replace(self, rejection=rejection)


def weighted_score(features: FeatureScores, weights: FeatureWeights) -> float:
    """The weighted sum of the features, within 0..1."""
    total = math.fsum(
        getattr(features, feature.value) * getattr(weights, feature.value)
        for feature in FeatureName
    )
    return round(min(1.0, max(0.0, total)), SCORE_DECIMALS)


def score_candidate(
    fingerprint: Fingerprint,
    candidate: LiveCandidate,
    origin: CandidateOrigin,
    config: HealingConfig,
    rejection: SafetyRejection | None = None,
) -> ScoredElement:
    """One candidate scored against the fingerprint."""
    features = feature_scores(fingerprint, candidate, config)
    return ScoredElement(
        candidate=candidate,
        origin=origin,
        features=features,
        score=weighted_score(features, config.weights),
        signature=candidate_signature(candidate),
        rejection=rejection,
    )


def rank(scored: Iterable[ScoredElement]) -> tuple[ScoredElement, ...]:
    """Best score first; ties ordered by signature, so input order never matters."""
    return tuple(sorted(scored, key=lambda item: (-item.score, item.signature, item.origin.value)))
