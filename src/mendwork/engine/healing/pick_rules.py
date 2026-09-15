"""Rules a model's pick must pass that Rung 2's winners do not face (ADR 0010).

A Rung 2 winner is backed by a score and a margin over every other candidate; a model's pick is
backed only by the model's judgement, which can be confidently wrong. These rules only ever refuse:
applied after every other safety rule, they can turn an accept into an abstention and never the
reverse.

- **Context veto.** When the recording has nearby text, a pick sharing none of it is refused. A
  control from another part of the page (the navigation bar instead of a card, say) is not the
  recorded control, however its name reads.
- **Weak verification.** A step whose checkpoints only check where it leads (``url_matches``) or
  what a field holds (``field_has_value``) cannot tell the recorded control from another that does
  the same thing: a navigation link and a card link to the same page both pass. On such a step a
  pick must keep the recorded ``id``, ``name``, or test id. Chosen from the Rung 3 census in
  ADR 0010: wording stopped none of the wrong elements a model was shown, because wording is how
  they reached the list; an identifier stopped all of them.
"""

from typing import Final

from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import RejectionReason, SafetyRejection
from mendwork.engine.domain.steps import Step
from mendwork.engine.healing.config import HealingConfig
from mendwork.engine.healing.features import nearby_text_similarity
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.replay.identity import normalize_name
from mendwork.engine.safety.heal_policy import verification_strength

IDENTITY_ATTRIBUTES: Final = (
    "id",
    "name",
    "data_testid",
    "autocomplete",
    "aria_label",
    "placeholder",
)
"""Recorded attributes that say which element something is. ``href`` is left out: it says where a
link goes, which another link to the same destination shares."""

IDENTIFIERS: Final = frozenset({"id", "name", "data_testid"})
"""The identity attributes a page author gives one control. ``autocomplete``, ``aria_label``, and
``placeholder`` describe a purpose or wording that other controls can share, so they do not clear
the weak-verification bar."""


def context_rejection(
    fingerprint: Fingerprint, candidate: LiveCandidate, config: HealingConfig
) -> SafetyRejection | None:
    """A pick sharing none of the recorded nearby text, refused; None otherwise."""
    if not fingerprint.nearby_text:
        return None
    if nearby_text_similarity(fingerprint, candidate, config.name_similarity_floor) > 0:
        return None
    return SafetyRejection(
        reason=RejectionReason.CONTEXT_LOST,
        detail=(
            "it shares none of the text recorded near the control, so it is a control from "
            "another part of the page"
        ),
    )


def verification_rejection(
    step: Step, fingerprint: Fingerprint, candidate: LiveCandidate
) -> SafetyRejection | None:
    """A pick keeping no recorded identifier on a step without strong checkpoints, refused."""
    if verification_strength(step.checkpoints) is VerificationStrength.STRONG:
        return None
    if IDENTIFIERS.intersection(surviving_identity_attributes(fingerprint, candidate)):
        return None
    return SafetyRejection(
        reason=RejectionReason.WEAK_VERIFICATION,
        detail=(
            "this step's checkpoints only check where it leads or what the field holds, which "
            "another control can do too, so a model's choice must keep the recorded id, name, or "
            "test id, and it keeps none"
        ),
    )


def surviving_identity_attributes(
    fingerprint: Fingerprint, candidate: LiveCandidate
) -> tuple[str, ...]:
    """The recorded identity attributes the candidate still has, with the same value."""
    recorded = fingerprint.attributes
    facts = candidate.facts
    pairs = {
        "id": (recorded.id, facts.id),
        "name": (recorded.name, facts.name),
        "data_testid": (recorded.data_testid, facts.data_testid),
        "autocomplete": (recorded.autocomplete, facts.autocomplete),
        "aria_label": (_text(recorded.aria_label), _text(facts.aria_label)),
        "placeholder": (_text(recorded.placeholder), _text(facts.placeholder)),
    }
    return tuple(
        attribute
        for attribute in IDENTITY_ATTRIBUTES
        if pairs[attribute][0] and pairs[attribute][0] == pairs[attribute][1]
    )


def _text(value: str | None) -> str | None:
    return normalize_name(value) or None
