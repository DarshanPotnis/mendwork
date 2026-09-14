"""Heal evidence: what each rung of the ladder examined, what it chose, and why.

A heal is a proposal until the step's checkpoints pass, so every attempt records its
verification outcome, and an abstention records the reason nothing was acted on. Every
number a decision used is kept (each feature's score, the total, the margin, the threshold
and margin it was held to), so any decision can be recomputed and explained later. Rung 3
also keeps what it showed the model and what the model answered.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.identifiers import StepIdField
from mendwork.engine.domain.model_evidence import ModelChoiceEvidence
from mendwork.engine.domain.scores import CandidateId, Score
from mendwork.engine.domain.targets import IdentityReport, TargetEvidence

HealedRung = Literal[1, 2, 3]
"""A rung that can accept a heal."""


class FeatureName(StrEnum):
    """One clue Rung 2 compares between the recorded fingerprint and a live element."""

    NAME = "name"
    LABEL = "label"
    ATTRIBUTES = "attributes"
    ROLE = "role"
    TAG_TYPE = "tag_type"
    NEARBY_TEXT = "nearby_text"
    STRUCTURAL_PATH = "structural_path"
    POSITION = "position"


class FeatureScores(DomainModel):
    """Each feature's similarity, from 0 (nothing in common) to 1 (the same)."""

    name: Score
    label: Score
    attributes: Score
    role: Score
    tag_type: Score
    nearby_text: Score
    structural_path: Score
    position: Score


class CandidateOrigin(StrEnum):
    """How an element came to be compared."""

    PAGE = "page"
    """Found by scanning the page for elements the action could receive."""
    RUNG0_DRIFTED = "rung0_drifted"
    """The element the recorded selectors agreed on, whose identity had drifted."""
    RUNG1 = "rung1"
    """The element Rung 1's alternate selectors agreed on."""


class RejectionReason(StrEnum):
    """A safety rule that refuses a candidate whatever it scores."""

    DANGER_WORD = "danger_word"
    """Its name or text adds a danger word the recorded name did not have."""
    IDENTIFIER_MISMATCH = "identifier_mismatch"
    """Its name carries a different identifier, such as another order number."""
    KIND_CHANGED = "kind_changed"
    """It is a different kind of control, or a change of kind the step cannot verify."""
    CREDENTIAL_MISMATCH = "credential_mismatch"
    """A credential would be typed into a visible field, or a plain value into a masked one."""
    UNCONFIRMED_IDENTITY = "unconfirmed_identity"
    """Playwright could not confirm the identity computed for it, or it changed since."""
    LOOK_ALIKE = "look_alike"
    """Another candidate reads exactly the same in what the model was shown."""
    CONTEXT_LOST = "context_lost"
    """A model's pick shares none of the text recorded near the control."""
    WEAK_VERIFICATION = "weak_verification"
    """A model's pick lacks the corroboration a step with weak checkpoints requires."""


class SafetyRejection(DomainModel):
    """Why a safety rule refused a candidate, in words a reviewer can check."""

    reason: RejectionReason
    detail: str


class ScoredCandidate(DomainModel):
    """One compared element: who it is, how similar it is, and whether a rule refused it."""

    id: CandidateId
    origin: CandidateOrigin
    identity: IdentityReport
    score: Score
    features: FeatureScores
    rejection: SafetyRejection | None = None


class RungOutcome(StrEnum):
    """What one rung concluded."""

    RESOLVED = "resolved"
    DRIFTED = "drifted"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    NO_CANDIDATES = "no_candidates"
    BELOW_THRESHOLD = "below_threshold"
    BELOW_MARGIN = "below_margin"
    TOP_REJECTED = "top_rejected"
    CANDIDATE_CAP_REACHED = "candidate_cap_reached"
    PAGE_NEVER_STABLE = "page_never_stable"
    NO_ELIGIBLE = "no_eligible"
    """Rung 3: no candidate both passed the safety rules and shared wording or identity
    attributes with the recording, so the model was not asked."""
    LOOK_ALIKES = "look_alikes"
    """Rung 3: the best eligible candidate reads exactly like another, so the model was not
    asked."""
    NOT_ASKED = "not_asked"
    """Rung 3: a gate that does not depend on the choice already stops this step."""
    MODEL_ABSTAINED = "model_abstained"
    CHOICE_OUT_OF_RANGE = "choice_out_of_range"
    OUTPUT_INVALID = "output_invalid"
    MODEL_UNAVAILABLE = "model_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CHOICE_REFUSED = "choice_refused"


class Verification(StrEnum):
    """Whether an accepted heal was proven by the step's checkpoints."""

    NOT_PERFORMED = "not_performed"
    """Nothing was acted on in this attempt."""
    PENDING = "pending"
    """The heal was accepted and the action is about to run."""
    PASSED = "passed"
    FAILED = "failed"


class HealAttemptReport(DomainModel):
    """One rung's work within one heal attempt of a step."""

    rung: Literal[0, 1, 2, 3]
    attempt: int = Field(ge=1)
    """Which pass through the ladder this is; a failed verification starts another."""
    outcome: RungOutcome
    target: TargetEvidence | None = None
    """Selector evidence: the recorded selectors at Rung 0, the alternates at Rung 1."""
    candidates: tuple[ScoredCandidate, ...] = ()
    """The best-scoring candidates, best first; at Rung 3, the ones shown to the model."""
    considered: int = Field(default=0, ge=0)
    """How many candidates were scored; at Rung 3, how many were eligible."""
    on_page: int = Field(default=0, ge=0)
    """How many action-compatible visible elements the page had."""
    chosen: CandidateId | None = None
    runner_up: CandidateId | None = None
    """The best other candidate no safety rule refused."""
    score: Score | None = None
    margin: Score | None = None
    threshold: Score | None = None
    required_margin: Score | None = None
    kind_change: str | None = None
    """A change of element kind the heal accepts, such as ``button → link``."""
    verification: Verification = Verification.NOT_PERFORMED
    model: ModelChoiceEvidence | None = None
    """Rung 3 only: what the model was shown and answered."""


class AbstentionReason(StrEnum):
    """Why the ladder acted on nothing."""

    NO_CANDIDATES = "no_candidates"
    BELOW_THRESHOLD = "below_threshold"
    BELOW_MARGIN = "below_margin"
    TOP_REJECTED = "top_rejected"
    CANDIDATE_CAP_REACHED = "candidate_cap_reached"
    PAGE_NEVER_STABLE = "page_never_stable"
    UNVERIFIABLE = "unverifiable"
    AUTHENTICATION_LIMIT = "authentication_limit"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    RESTORE_FAILED = "restore_failed"
    HEAL_TIMED_OUT = "heal_timed_out"
    MODEL_ABSTAINED = "model_abstained"
    MODEL_CHOICE_OUT_OF_RANGE = "model_choice_out_of_range"
    MODEL_OUTPUT_INVALID = "model_output_invalid"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_BUDGET_EXHAUSTED = "model_budget_exhausted"
    MODEL_CHOICE_REFUSED = "model_choice_refused"


class RecoveryReport(DomainModel):
    """Putting the page back to its last known-good state after a heal failed verification."""

    after_attempt: int = Field(ge=1)
    url: str
    """Where the page was re-opened: the start of the first step on the failed step's page."""
    replayed: tuple[StepIdField, ...]
    """The steps replayed on the re-opened page, in order."""
    cleared_field: bool
    """Whether the field a failed fill typed into was emptied first."""
    restored: bool
    reason: str | None = None
    """Why the state could not be restored."""


class HealProposal(DomainModel):
    """A heal found for a step that may not act without a person's approval."""

    rung: HealedRung
    candidate: ScoredCandidate
    margin: Score | None = None
    reason: str
    model: ModelChoiceEvidence | None = None
    """For a Rung 3 proposal, what the model was shown and answered."""


class HealReport(DomainModel):
    """Everything the ladder did for one step."""

    attempts: tuple[HealAttemptReport, ...] = ()
    recoveries: tuple[RecoveryReport, ...] = ()
    healed_rung: HealedRung | None = None
    """The rung whose heal passed verification."""
    abstention: AbstentionReason | None = None
    proposal: HealProposal | None = None
