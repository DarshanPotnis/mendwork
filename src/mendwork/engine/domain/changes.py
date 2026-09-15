"""Change records: why a workflow version other than the first exists.

A discriminated union, so each kind of change carries exactly its own evidence. Every ``match``
over ``ChangeRecord`` fails type checking until it handles every kind, which is how a new kind
stays safe without a placeholder.

A heal (ADR 0013) records the step's target before and after, the rung that found the new one, the
numbers that accepted it or what asking a model cost, the checkpoints that verified it and how
much they prove, the approval an irreversible step needed, and where the run's evidence is.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from mendwork.engine.domain.base import DomainModel, ShortText, Text
from mendwork.engine.domain.enums import (
    ChangeKind,
    CheckpointKind,
    PromotionPolicy,
    VerificationStrength,
)
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import PROPOSAL_ID_PATTERN, HealedRung
from mendwork.engine.domain.identifiers import StepIdField, VersionNumber
from mendwork.engine.domain.limits import CHECKPOINTS_MAX_ITEMS
from mendwork.engine.domain.model_evidence import TokenCount, Usd
from mendwork.engine.domain.run_identifiers import ArtifactNameField, RunIdField
from mendwork.engine.domain.scores import Score

MODEL_RUNG: Final = 3


class ManualEdit(DomainModel):
    """A person edited the workflow's content by hand."""

    kind: Literal[ChangeKind.MANUAL_EDIT]
    summary: Text
    """What changed and why, for the version history."""


class Rollback(DomainModel):
    """The content of an earlier version was restored as a new version."""

    kind: Literal[ChangeKind.ROLLBACK]
    restored_version: VersionNumber
    """The version whose content this version carries."""
    reason: Text


class ModelHealUsage(DomainModel):
    """What asking a model for a heal cost, and how sure it said it was."""

    provider: Text
    model: Text
    prompt_version: ShortText
    calls: int = Field(default=0, ge=0)
    """Calls made for the step's heal, repairs included; 0 when an approved pick was found again."""
    input_tokens: TokenCount = 0
    output_tokens: TokenCount = 0
    estimated_cost_usd: Usd = Decimal(0)
    unpriced_calls: int = Field(default=0, ge=0)
    unreported_token_calls: int = Field(default=0, ge=0)
    """Calls whose provider reported no token counts, which the token counts above leave out."""
    confidence: Score | None = None
    """The model's own confidence: evidence only, no decision reads it."""


class ApprovalReference(DomainModel):
    """The approval that let an irreversible step act on its heal."""

    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    audit_sequence: int = Field(ge=1)
    """The audit log entry that records the approval."""
    decided_at: datetime


class HealEvidence(DomainModel):
    """Where the run that verified the heal keeps its evidence, relative to its artifacts."""

    run_id: RunIdField
    report: ArtifactNameField | None = None
    step_screenshot: ArtifactNameField | None = None
    found_screenshot: ArtifactNameField | None = None
    """The element, outlined in the run's report, just before the action."""


class Promotion(DomainModel):
    """How the heal became a version."""

    policy: PromotionPolicy
    runs: tuple[RunIdField, ...] = Field(min_length=1)
    """Every succeeded run that verified it, the healing run first."""


class HealChange(DomainModel):
    """A verified heal: one step now targets the element the heal found and verified."""

    kind: Literal[ChangeKind.HEAL]
    step_id: StepIdField
    rung: HealedRung
    old_target: Fingerprint
    """The step's target in the version the heal was verified against."""
    new_target: Fingerprint
    """The healed element, fingerprinted by the recorder's own derivation."""
    checkpoints: tuple[CheckpointKind, ...] = Field(min_length=1, max_length=CHECKPOINTS_MAX_ITEMS)
    """The checkpoints that passed on the healed element, in the step's order."""
    strength: VerificationStrength
    """Strong when a checkpoint observed something only the right action produces; weak when only
    ``url_matches`` or ``field_has_value`` did, which another element could pass too."""
    score: Score | None = None
    margin: Score | None = None
    threshold: Score | None = None
    required_margin: Score | None = None
    model: ModelHealUsage | None = None
    approval: ApprovalReference | None = None
    evidence: HealEvidence
    promotion: Promotion

    @model_validator(mode="after")
    def _describes_a_real_change(self) -> Self:
        if self.new_target == self.old_target:
            raise ValueError("a heal changes the step's target")
        if self.model is not None and self.rung != MODEL_RUNG:
            raise ValueError(f"only a rung {MODEL_RUNG} heal records model usage")
        if self.strength is VerificationStrength.NONE:
            raise ValueError("a heal is verified by at least one checkpoint that can prove it")
        return self


ChangeRecord = Annotated[ManualEdit | Rollback | HealChange, Field(discriminator="kind")]


def describe_change(change: ChangeRecord) -> str:
    """A one-line account of a change, for version history listings."""
    match change:
        case ManualEdit():
            return f"manual edit: {change.summary}"
        case Rollback():
            return f"rollback to v{change.restored_version}: {change.reason}"
        case HealChange():
            return f"healed step {change.step_id} at rung {change.rung}"
