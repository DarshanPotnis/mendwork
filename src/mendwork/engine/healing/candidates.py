"""Live candidates: the contract with the browser adapter, and what the engine makes of it.

The adapter's scan (``BrowserPort.scan_candidates``) returns, for each visible element the
action could receive, a pinned element, its identity as the page computes it (role and
accessible name, unconfirmed), and its facts (tag, attributes, label, visible text, nearby
text, structural path, and box), all read without any field's content. The scan's own
filter only narrows the list; this module decides which candidates an action accepts.

A candidate's *signature* is what identifies it across reloads of the same page: a restored
page has new element handles, but a candidate that failed verification must stay excluded.
Within one run the page is rebuilt on the same machine, so the signature includes the element's
position. An approval is matched on the *identity signature* instead, which leaves position out:
between a pause and an approval, a cookie banner, a viewport, or platform fonts can move an
element without changing what it is (ADR 0011).
"""

from dataclasses import dataclass
from typing import Final

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.replay.identity import normalize_name
from mendwork.engine.safety.heal_kinds import ElementKind, action_accepts, interaction_class
from mendwork.engine.safety.secret_scrub import SecretScrubber

CandidateSignature = tuple[str, ...]
_BOX_DECIMALS: Final = 3


def recorded_kind(fingerprint: Fingerprint) -> ElementKind:
    """How the recorded element was used."""
    return ElementKind(
        tag=fingerprint.tag,
        role=fingerprint.role.value if fingerprint.role is not None else None,
        input_type=fingerprint.attributes.type,
        has_href=fingerprint.attributes.href is not None,
        text_entry=fingerprint.tag == "textarea",
    )


def found_kind(candidate: LiveCandidate) -> ElementKind:
    """How a live element is used."""
    return ElementKind(
        tag=candidate.identity.tag,
        role=candidate.identity.role,
        input_type=candidate.identity.input_type,
        has_href=candidate.facts.href is not None,
        text_entry=candidate.facts.text_entry,
    )


def compatible(action: ActionType, candidate: LiveCandidate) -> bool:
    """Whether the action can be performed on the candidate at all."""
    return action_accepts(action, interaction_class(found_kind(candidate)))


def candidate_signature(candidate: LiveCandidate) -> CandidateSignature:
    """What identifies a candidate on a reloaded page, and orders ties deterministically.

    The element's position is the last part, so ``identity_signature`` can leave it out.
    """
    identity = candidate.identity
    facts = candidate.facts
    box = facts.box
    position = (
        ""
        if box is None
        else ",".join(
            f"{value:.{_BOX_DECIMALS}f}" for value in (box.x, box.y, box.width, box.height)
        )
    )
    return (
        identity.tag,
        identity.role or "",
        normalize_name(identity.name),
        identity.input_type or "",
        facts.id or "",
        facts.name or "",
        facts.data_testid or "",
        facts.href or "",
        facts.structural_path,
        " | ".join(facts.nearby_text),
        position,
    )


def identity_signature(signature: CandidateSignature) -> CandidateSignature:
    """A signature without the element's position."""
    return signature[:-1]


def approved_identity(
    signature: CandidateSignature, scrubber: SecretScrubber
) -> CandidateSignature:
    """What a proposal records and an approval must still match: the identity, scrubbed."""
    return tuple(scrubber.scrub_text(part) for part in identity_signature(signature))


@dataclass(frozen=True, slots=True)
class SignatureMatch:
    """A remembered element to find again."""

    signature: CandidateSignature
    by_identity: SecretScrubber | None = None
    """When set, ``signature`` is an approved identity: a candidate matches on its own identity,
    scrubbed with this scrubber, wherever the element now sits."""

    def matches(self, other: CandidateSignature) -> bool:
        """Whether a candidate with this full signature is the remembered element."""
        if self.by_identity is None:
            return other == self.signature
        return approved_identity(other, self.by_identity) == self.signature
