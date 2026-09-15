"""Run events: the versioned stream a run emits as it happens.

Every event carries ``event_version``, the run id, a sequence number that starts at 1 and
has no gaps, and a UTC timestamp from the Clock port. Consumers dispatch on ``type``. A
new field is additive; renaming or removing one changes ``event_version``.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import ActionType, RiskLevel, ValueKind
from mendwork.engine.domain.heals import HealAttemptReport, HealedRung, RecoveryReport
from mendwork.engine.domain.identifiers import StepIdField, VersionNumber, WorkflowIdField
from mendwork.engine.domain.model_evidence import ModelUsageTotals
from mendwork.engine.domain.runs import (
    CheckpointResult,
    ErrorReport,
    NavigationReport,
    RunIdField,
    RunStatus,
    StepArtifacts,
    StepStatus,
)
from mendwork.engine.domain.targets import TargetEvidence


class _RunEvent(DomainModel):
    event_version: Literal[1] = 1
    run_id: RunIdField
    sequence: int = Field(ge=1)
    at: datetime


class _StepEvent(_RunEvent):
    step_id: StepIdField
    index: int = Field(ge=0)


class RunStartedEvent(_RunEvent):
    """The run passed its preflight checks and is about to open a browser."""

    type: Literal["run_started"] = "run_started"
    workflow_id: WorkflowIdField
    workflow_version: VersionNumber
    step_count: int = Field(ge=1)
    input_names: tuple[str, ...]
    secret_names: tuple[str, ...]


class RunResumedEvent(_RunEvent):
    """A person approved a proposal and the run resumed in a new browser.

    The steps before the approved one are replayed without events to rebuild its page; the
    approved step's own events follow.
    """

    type: Literal["run_resumed"] = "run_resumed"
    workflow_id: WorkflowIdField
    workflow_version: VersionNumber
    step_count: int = Field(ge=1)
    segment: int = Field(ge=2)
    """Which execution of the run this is, counting from 1."""
    proposal_id: str
    step_id: StepIdField
    """The approved step."""
    index: int = Field(ge=0)


class StepStartedEvent(_StepEvent):
    """A step began."""

    type: Literal["step_started"] = "step_started"
    action: ActionType
    risk: RiskLevel
    intent: str


class TargetResolvedEvent(_StepEvent):
    """The step's target was found: by Rung 0 with a verified identity, or by an accepted heal."""

    type: Literal["target_resolved"] = "target_resolved"
    evidence: TargetEvidence


class HealAttemptedEvent(_StepEvent):
    """One rung of the heal ladder decided, before anything acts on its decision."""

    type: Literal["heal_attempted"] = "heal_attempted"
    report: HealAttemptReport


class HealVerifiedEvent(_StepEvent):
    """The checkpoints ran after acting on a healed target: the heal is proven or refuted."""

    type: Literal["heal_verified"] = "heal_verified"
    rung: HealedRung
    attempt: int = Field(ge=1)
    passed: bool
    failed_checkpoint: CheckpointResult | None = None


class StateRestoredEvent(_StepEvent):
    """After a heal failed verification, the page was put back to its last known-good state."""

    type: Literal["state_restored"] = "state_restored"
    recovery: RecoveryReport


class ActionPerformedEvent(_StepEvent):
    """The step's action reached the page. Values are never included, only their kind."""

    type: Literal["action_performed"] = "action_performed"
    action: ActionType
    value_kind: ValueKind | None = None
    navigation: NavigationReport | None = None


class CheckpointPassedEvent(_StepEvent):
    """A checkpoint passed."""

    type: Literal["checkpoint_passed"] = "checkpoint_passed"
    checkpoint: CheckpointResult


class CheckpointFailedEvent(_StepEvent):
    """A checkpoint failed; the step fails with it."""

    type: Literal["checkpoint_failed"] = "checkpoint_failed"
    checkpoint: CheckpointResult


class StepSucceededEvent(_StepEvent):
    """A step's action ran and every checkpoint passed."""

    type: Literal["step_succeeded"] = "step_succeeded"
    duration_ms: int = Field(ge=0)
    artifacts: StepArtifacts


class StepFailedEvent(_StepEvent):
    """A step did not succeed; the run stops here."""

    type: Literal["step_failed"] = "step_failed"
    status: StepStatus = StepStatus.FAILED
    """Failed, or stopped for a person: awaiting approval or needing review."""
    duration_ms: int = Field(ge=0)
    action_performed: bool
    error: ErrorReport
    target: TargetEvidence | None = None
    artifacts: StepArtifacts


class RunFinishedEvent(_RunEvent):
    """The run ended; the run record has its final status."""

    type: Literal["run_finished"] = "run_finished"
    status: RunStatus
    duration_ms: int = Field(ge=0)
    failed_step_id: StepIdField | None = None
    error_type: str | None = None
    model_usage: ModelUsageTotals = ModelUsageTotals()
    """Every model call the run made, added up."""


RunEvent = Annotated[
    RunStartedEvent
    | RunResumedEvent
    | StepStartedEvent
    | TargetResolvedEvent
    | HealAttemptedEvent
    | HealVerifiedEvent
    | StateRestoredEvent
    | ActionPerformedEvent
    | CheckpointPassedEvent
    | CheckpointFailedEvent
    | StepSucceededEvent
    | StepFailedEvent
    | RunFinishedEvent,
    Field(discriminator="type"),
]
