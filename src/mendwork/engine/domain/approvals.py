"""Approvals: a person's decision on a proposed heal, and what came of it (ADR 0011).

An irreversible step never acts on a heal by itself: the run stops with a proposal. A person then
approves it, and the run resumes and acts only if the page still shows the approved element, or
rejects it, and the run ends failed. Every proposal the run made stays in its record with the
decision and the outcome, so nothing about an approval has to be reconstructed later.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.heals import HealedRung, HealProposal
from mendwork.engine.domain.identifiers import StepIdField

REASON_MAX_LENGTH = 500
"""The longest reason a rejection may give; enough for a sentence, too short for a pasted page."""


class DecisionKind(StrEnum):
    """What a person decided."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ProposalDecision(DomainModel):
    """A decision, as the audit log recorded it."""

    kind: DecisionKind
    at: datetime
    audit_sequence: int = Field(ge=1)
    """The audit log entry that records the decision."""
    reason: str | None = None


class ProposalOutcome(StrEnum):
    """What came of an approval."""

    ACTED_VERIFIED = "acted_verified"
    """The approved element was found again, acted on, and the step's checkpoints passed."""
    ACTED_UNVERIFIED = "acted_unverified"
    """The approved element was acted on and a checkpoint failed; the run needs review."""
    NOT_NEEDED = "not_needed"
    """The recorded target resolved again, so the step ran as recorded, without the heal."""
    STALE = "stale"
    """The page no longer showed the approved element; nothing was acted on."""
    INTERRUPTED = "interrupted"
    """The resume was interrupted before the approved step finished."""
    NOT_RESUMED = "not_resumed"
    """The approval was recorded, but the command stopped before the run resumed."""


class StaleReason(StrEnum):
    """Why an approved target no longer matched."""

    PAGE_NOT_REESTABLISHED = "page_not_reestablished"
    """An earlier step, replayed to rebuild the page, did not succeed again."""
    APPROVED_TARGET_NOT_FOUND = "approved_target_not_found"
    DIFFERENT_TARGET = "different_target"
    """The ladder accepted another element."""
    IDENTITY_CHANGED = "identity_changed"
    """The element matched on what it is, but its confirmed identity differs."""
    REFUSED = "refused"
    """A safety rule now refuses the approved element."""
    NO_LONGER_ACCEPTED = "no_longer_accepted"
    """The element no longer clears the score threshold or margin."""
    PAGE_NEVER_STABLE = "page_never_stable"


class ProposalRecord(DomainModel):
    """A proposal a run made, with the decision on it and what came of that."""

    proposal: HealProposal
    decision: ProposalDecision | None = None
    outcome: ProposalOutcome | None = None
    stale_reason: StaleReason | None = None
    detail: str | None = None
    """Words for the outcome, such as the rule that refused the element."""

    @property
    def pending(self) -> bool:
        """Whether nobody has decided on it yet."""
        return self.decision is None


class VerifiedHealRecord(DomainModel):
    """An earlier step's verified heal, found again by what it is as a resume rebuilds the page."""

    step_id: StepIdField
    rung: HealedRung
    identity_signature: tuple[str, ...]


class ResumeState(DomainModel):
    """What a resume needs that the steps' results do not say."""

    inputs_recoverable: bool
    """False when an input held a secret's value: the record keeps only the scrubbed form, so the
    run cannot be resumed with the original."""
    verified_heals: tuple[VerifiedHealRecord, ...] = ()
