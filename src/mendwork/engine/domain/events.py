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
from mendwork.engine.domain.identifiers import StepIdField, VersionNumber, WorkflowIdField
from mendwork.engine.domain.runs import (
    CheckpointResult,
    ErrorReport,
    NavigationReport,
    RunIdField,
    RunStatus,
    StepArtifacts,
    TargetEvidence,
)


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


class StepStartedEvent(_StepEvent):
    """A step began."""

    type: Literal["step_started"] = "step_started"
    action: ActionType
    risk: RiskLevel
    intent: str


class TargetResolvedEvent(_StepEvent):
    """Rung 0 found the step's target and verified its identity."""

    type: Literal["target_resolved"] = "target_resolved"
    evidence: TargetEvidence


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
    """A step failed; the run stops here."""

    type: Literal["step_failed"] = "step_failed"
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


RunEvent = Annotated[
    RunStartedEvent
    | StepStartedEvent
    | TargetResolvedEvent
    | ActionPerformedEvent
    | CheckpointPassedEvent
    | CheckpointFailedEvent
    | StepSucceededEvent
    | StepFailedEvent
    | RunFinishedEvent,
    Field(discriminator="type"),
]
