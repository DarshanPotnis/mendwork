"""Run records: what happened when a workflow version was replayed, step by step.

A run record is evidence. It says which selector found each target and what identity the
element had, which checkpoints passed, what failed and why, and where the screenshots,
DOM snapshots, and traces are. Secret values never appear in it: every string that came
from the page or from an error passes through the run's secret scrubber first.

While a run is in progress its record is kept current on disk (ADR 0011): it is rewritten when the
run starts, after every step, just before an irreversible action is dispatched, and when the run
finishes, so a record left behind by a process that stopped still says what happened.

Step indexes are zero-based in records and events; people see them one-based.
"""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import Field, JsonValue

from mendwork.engine.domain.approvals import ProposalRecord, ResumeState
from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import ActionType, CheckpointKind
from mendwork.engine.domain.heals import HealReport
from mendwork.engine.domain.identifiers import (
    SecretNameField,
    StepIdField,
    VersionNumber,
    WorkflowIdField,
)
from mendwork.engine.domain.model_evidence import ModelUsageTotals
from mendwork.engine.domain.patches import FoundTarget, PatchOutcome, WorkflowSource
from mendwork.engine.domain.run_identifiers import ArtifactName as ArtifactName
from mendwork.engine.domain.run_identifiers import ArtifactNameField as ArtifactNameField
from mendwork.engine.domain.run_identifiers import RunId as RunId
from mendwork.engine.domain.run_identifiers import RunIdField as RunIdField
from mendwork.engine.domain.run_identifiers import parse_artifact_name as parse_artifact_name
from mendwork.engine.domain.run_identifiers import parse_run_id as parse_run_id
from mendwork.engine.domain.targets import TargetEvidence


class RunStatus(StrEnum):
    """Where a run is in its lifecycle."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    """The run was interrupted before it finished, and it had dispatched no irreversible action."""
    AWAITING_APPROVAL = "awaiting_approval"
    """A heal was found for an irreversible step; nothing acts on it without approval."""
    NEEDS_REVIEW = "needs_review"
    """An irreversible action ran and could not be verified, or the run was interrupted after one
    was dispatched: a person must check it, and it is never re-run automatically."""


class StepStatus(StrEnum):
    """What happened to one step."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    """The run was interrupted while this step was in progress."""
    AWAITING_APPROVAL = "awaiting_approval"
    NEEDS_REVIEW = "needs_review"
    NOT_RUN = "not_run"


STOPPING_STATUSES: Final = frozenset(
    {
        StepStatus.FAILED,
        StepStatus.CANCELLED,
        StepStatus.AWAITING_APPROVAL,
        StepStatus.NEEDS_REVIEW,
    }
)
"""A step that ends in one of these stops the run."""


class ErrorCategory(StrEnum):
    """Whether a failure belongs to the workflow and the site, or to Mendwork's own machinery."""

    STEP = "step"
    INFRASTRUCTURE = "infrastructure"


class TraceWithheldReason(StrEnum):
    """Why a failure's trace was not saved."""

    SECRET_BEARING_PAGE = "secret_bearing_page"  # noqa: S105 - a reason code, not a credential
    """The failing page still held a value typed from a secret, so its trace could contain it."""
    SECRET_DETECTED = "secret_detected"  # noqa: S105 - a reason code, not a credential
    """The trace was scanned, found to contain a secret value, and deleted."""


class CheckpointResult(DomainModel):
    """One checkpoint's verdict."""

    index: int = Field(ge=0)
    """The checkpoint's position in the step, as written."""
    kind: CheckpointKind
    passed: bool
    reason: str | None = None
    detail: str | None = None


class ErrorReport(DomainModel):
    """A failure, as data: the error type, its message, and its structured context."""

    type: str
    message: str
    category: ErrorCategory
    context: Mapping[str, JsonValue] = Field(default_factory=dict)


class TraceWithheld(DomainModel):
    """A failure trace that was deliberately not saved, and why."""

    reason: TraceWithheldReason
    typed_at_step: StepIdField | None = None
    """The step that typed the value the failing page still held."""
    typed_at_index: int | None = None


class StepArtifacts(DomainModel):
    """Where a step's evidence was stored, relative to its run's artifacts."""

    screenshot: ArtifactNameField | None = None
    dom_snapshot: ArtifactNameField | None = None
    trace: ArtifactNameField | None = None
    trace_withheld: TraceWithheld | None = None
    download: ArtifactNameField | None = None
    capture_errors: tuple[str, ...] = ()
    """Evidence that could not be captured, so a missing file is never a mystery."""


class NavigationReport(DomainModel):
    """The outcome of a navigate step."""

    url: str
    status: int | None = None
    attempts: int = Field(ge=1)


class StepResult(DomainModel):
    """What happened to one step of a run."""

    step_id: StepIdField
    index: int = Field(ge=0)
    action: ActionType
    status: StepStatus
    started_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    target: TargetEvidence | None = None
    navigation: NavigationReport | None = None
    action_performed: bool = False
    """Whether the step's action reached the page. False on every Rung 0 failure."""
    action_outcome_unknown: bool = False
    """The run was interrupted while the action was being sent, so whether it reached the page is
    not known."""
    checkpoints: tuple[CheckpointResult, ...] = ()
    error: ErrorReport | None = None
    artifacts: StepArtifacts = StepArtifacts()
    heal: HealReport | None = None
    """What the heal ladder did, when the recorded selectors could not safely proceed."""
    found: FoundTarget | None = None
    """The verified heal's element, fingerprinted as the recorder would record it (ADR 0013)."""


class RunSegmentKind(StrEnum):
    """Why a run was executed."""

    RUN = "run"
    """The run's first execution."""
    RESUME = "resume"
    """An execution resumed by an approval."""


class RunSegment(DomainModel):
    """One execution of a run, with the egress policy it was held to."""

    kind: RunSegmentKind
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    proposal_id: str | None = None
    """For a resume, the approved proposal it resumed."""
    egress_allowed_domains: tuple[str, ...] = ()
    egress_loopback_exceptions: tuple[str, ...] = ()
    """Loopback origins the execution could reach, as ``ip:port``; local test targets only."""


class IrreversibleDispatch(DomainModel):
    """An irreversible action Mendwork was about to send, recorded before it was sent."""

    step_id: StepIdField
    index: int = Field(ge=0)
    at: datetime
    segment: int = Field(ge=1)
    """Which execution of the run dispatched it, counting from 1."""


class Run(DomainModel):
    """One replay of one workflow version: the record saved as ``run.json``."""

    record_version: Literal[1, 2] = 2
    """2 from Phase 7: records carry their workflow snapshot, segments, journaled dispatches, and
    proposals. Version 1 records are still read, but cannot be approved or resumed."""
    run_id: RunIdField
    workflow_id: WorkflowIdField
    workflow_version: VersionNumber
    workflow_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    """The digest of ``workflow.json``, the exact version the run executes, saved beside it."""
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    """Time spent executing, summed over segments; time waiting for approval is not counted."""
    inputs: Mapping[str, str] = Field(default_factory=dict)
    """Run inputs, including defaults applied. Secrets are listed by name only."""
    secrets: tuple[SecretNameField, ...] = ()
    steps: tuple[StepResult, ...] = ()
    """One result per step of the workflow, in order; steps after the one the run stopped at
    are not_run."""
    error: ErrorReport | None = None
    model_usage: ModelUsageTotals = ModelUsageTotals()
    """Every model call the run made, added up; zero when no step needed Rung 3."""
    segments: tuple[RunSegment, ...] = ()
    irreversible_dispatched: tuple[IrreversibleDispatch, ...] = ()
    """Every irreversible action dispatched, recorded before it was sent."""
    last_event_sequence: int = Field(default=0, ge=0)
    """The sequence number of the last event emitted, so a resume continues without a gap."""
    proposals: tuple[ProposalRecord, ...] = ()
    """Every heal proposal the run made, with the decision on it and what came of that."""
    resume: ResumeState | None = None
    """What an approval needs to resume the run; set when it pauses for approval."""
    paused_steps: tuple[StepResult, ...] = ()
    """Each step result a decision replaced, as it was when the run paused for approval."""
    source: WorkflowSource | None = None
    """Where the executed version came from, and whether its heals may become versions."""
    patches: tuple[PatchOutcome, ...] = ()
    """What came of each verified heal and pending patch when the run finished (ADR 0013)."""

    @property
    def failed_step(self) -> StepResult | None:
        """The step the run stopped at: failed, cancelled, awaiting approval, or needing review."""
        return next((step for step in self.steps if step.status in STOPPING_STATUSES), None)
