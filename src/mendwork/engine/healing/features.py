"""Rung 2's features: how similar one live element is to the recorded fingerprint, clue by clue.

Every function is pure and returns a value from 0 to 1. Text is compared after Rung 0's
normalization (NFKC, case folding, collapsed whitespace) with rapidfuzz's token-sort ratio,
then rescaled above a noise floor: unrelated labels share letters by chance ("Export ledger"
and "Invite teammate" score well below 0.5), and chance agreement must count as nothing.

A clue the recording did not have scores 0: absence is never evidence. Three features stand
in for a partner when their own clue cannot exist, so each group of clues keeps its weight:

- a fingerprint without a role (password and date inputs) compares kinds by tag and type,
  as Rung 0's identity check does;
- a fingerprint without a label (buttons, links) scores its label feature by name;
- a fingerprint without nearby text scores that feature by structural path.
"""

import math
from collections.abc import Sequence
from typing import Final

from rapidfuzz import fuzz
from rapidfuzz.distance import Indel

from mendwork.engine.domain.fingerprint import Fingerprint, NormalizedBox
from mendwork.engine.domain.heals import FeatureScores
from mendwork.engine.healing.config import HealingConfig
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.ports.element_types import Box
from mendwork.engine.replay.identity import effective_type, normalize_name

ACTIVATION_FAMILY: Final = frozenset({"button", "link", "menuitem"})
"""Roles a person activates the same way; a change within the family earns partial credit."""
_PATH_SEPARATOR: Final = " > "


def text_similarity(recorded: str | None, found: str | None, floor: float) -> float:
    """How alike two texts are, with agreement at or below ``floor`` counted as none."""
    first = normalize_name(recorded)
    second = normalize_name(found)
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    ratio = fuzz.token_sort_ratio(first, second) / 100
    return _clamp((ratio - floor) / (1.0 - floor))


def name_similarity(fingerprint: Fingerprint, candidate: LiveCandidate, floor: float) -> float:
    """Accessible names, or visible text where an element has no name."""
    return text_similarity(
        fingerprint.accessible_name or fingerprint.text,
        candidate.identity.name or candidate.facts.text,
        floor,
    )


def label_similarity(fingerprint: Fingerprint, candidate: LiveCandidate, floor: float) -> float:
    """Label texts; for a fingerprint with no label, the name similarity."""
    if fingerprint.label_text is None:
        return name_similarity(fingerprint, candidate, floor)
    return text_similarity(fingerprint.label_text, candidate.facts.label_text, floor)


def attribute_overlap(fingerprint: Fingerprint, candidate: LiveCandidate) -> float:
    """The share of recorded identity attributes the candidate has with the same value.

    Identifiers compare exactly: ``invoice-7780`` is not ``invoice-2231``, however alike. The
    type attribute is left to the tag and type feature, because it says what kind of element
    something is, not which one.
    """
    recorded = fingerprint.attributes
    facts = candidate.facts
    pairs = (
        (recorded.id, facts.id),
        (recorded.name, facts.name),
        (recorded.data_testid, facts.data_testid),
        (recorded.autocomplete, facts.autocomplete),
        (recorded.href, facts.href),
        (_text(recorded.aria_label), _text(facts.aria_label)),
        (_text(recorded.placeholder), _text(facts.placeholder)),
    )
    present = [(before, after) for before, after in pairs if before]
    if not present:
        return 0.0
    return sum(1 for before, after in present if before == after) / len(present)


def role_match(fingerprint: Fingerprint, candidate: LiveCandidate) -> float:
    """1 for the same role, 0.5 within the activation family, else 0."""
    if fingerprint.role is None:
        return tag_type_match(fingerprint, candidate)
    recorded = fingerprint.role.value
    found = candidate.identity.role
    if found == recorded:
        return 1.0
    if found in ACTIVATION_FAMILY and recorded in ACTIVATION_FAMILY:
        return 0.5
    return 0.0


def tag_type_match(fingerprint: Fingerprint, candidate: LiveCandidate) -> float:
    """1 for the same tag and effective type, 0.5 for the same tag only, else 0."""
    identity = candidate.identity
    if identity.tag.lower() != fingerprint.tag:
        return 0.0
    recorded_type = effective_type(fingerprint.tag, fingerprint.attributes.type)
    found_type = effective_type(identity.tag, identity.input_type)
    return 1.0 if recorded_type == found_type else 0.5


def structural_path_similarity(recorded: str, found: str) -> float:
    """How alike two ancestor chains are, level by level."""
    return _clamp(Indel.normalized_similarity(_levels(recorded), _levels(found)))


def nearby_text_similarity(
    fingerprint: Fingerprint, candidate: LiveCandidate, floor: float
) -> float:
    """For each recorded nearby text, its best match nearby the candidate, averaged."""
    recorded = fingerprint.nearby_text
    if not recorded:
        return structural_path_similarity(
            fingerprint.structural_path, candidate.facts.structural_path
        )
    found = candidate.facts.nearby_text
    if not found:
        return 0.0
    best = [max(text_similarity(text, other, floor) for other in found) for text in recorded]
    return _clamp(math.fsum(best) / len(best))


def position_proximity(recorded: NormalizedBox | None, found: Box | None, scale: float) -> float:
    """1 at the recorded position, falling to 0 at ``scale`` away (centre to centre)."""
    if recorded is None or found is None:
        return 0.0
    distance = math.hypot(
        (recorded.x + recorded.width / 2) - (found.x + found.width / 2),
        (recorded.y + recorded.height / 2) - (found.y + found.height / 2),
    )
    return _clamp(1.0 - distance / scale)


def feature_scores(
    fingerprint: Fingerprint, candidate: LiveCandidate, config: HealingConfig
) -> FeatureScores:
    """Every feature for one candidate."""
    floor = config.name_similarity_floor
    return FeatureScores(
        name=name_similarity(fingerprint, candidate, floor),
        label=label_similarity(fingerprint, candidate, floor),
        attributes=attribute_overlap(fingerprint, candidate),
        role=role_match(fingerprint, candidate),
        tag_type=tag_type_match(fingerprint, candidate),
        nearby_text=nearby_text_similarity(fingerprint, candidate, floor),
        structural_path=structural_path_similarity(
            fingerprint.structural_path, candidate.facts.structural_path
        ),
        position=position_proximity(fingerprint.bbox, candidate.facts.box, config.position_scale),
    )


def _levels(path: str) -> Sequence[str]:
    return [level.strip() for level in path.split(_PATH_SEPARATOR) if level.strip()]


def _text(value: str | None) -> str | None:
    return normalize_name(value) or None


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
