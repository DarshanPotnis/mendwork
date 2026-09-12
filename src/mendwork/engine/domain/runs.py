"""Run records: what happened when a workflow version was replayed, step by step.

A run record is evidence. It says which selector found each target and what identity the
element had, which checkpoints passed, what failed and why, and where the screenshots,
DOM snapshots, and traces are. Secret values never appear in it: every string that came
from the page or from an error passes through the run's secret scrubber first.

Step indexes are zero-based in records and events; people see them one-based.
"""

import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, NewType

from pydantic import Field, JsonValue, StringConstraints

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import ActionType, CheckpointKind, SelectorStrategy
from mendwork.engine.domain.identifiers import (
    SecretNameField,
    StepIdField,
    VersionNumber,
    WorkflowIdField,
)

_RUN_ID: Final = r"\d{8}T\d{6}Z-[0-9a-f]{8}"
_ARTIFACT_SEGMENT: Final = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
_ARTIFACT_NAME: Final = rf"{_ARTIFACT_SEGMENT}(?:/{_ARTIFACT_SEGMENT}){{0,3}}"

RunId = NewType("RunId", str)
"""Sortable and path-safe, such as ``20260911T141502Z-7c1e09ab``: UTC start time plus randomness."""
ArtifactName = NewType("ArtifactName", str)
"""A relative path inside one run's artifacts, such as ``steps/04_sign_in.png``.

Every segment starts with a letter or digit, so ``.`` and ``..`` cannot occur and a name
can never leave its run's directory.
"""

RunIdField = Annotated[RunId, StringConstraints(pattern=f"^{_RUN_ID}$")]
ArtifactNameField = Annotated[ArtifactName, StringConstraints(pattern=f"^{_ARTIFACT_NAME}$")]

# fullmatch, not "$": in Python "$" also matches before a trailing newline.
_RUN_ID_RE: Final = re.compile(_RUN_ID)
_ARTIFACT_NAME_RE: Final = re.compile(_ARTIFACT_NAME)


def parse_run_id(value: str) -> RunId:
    """Validate an untrusted string as a run id."""
    if _RUN_ID_RE.fullmatch(value) is None:
        raise ValueError("a run id looks like 20260911T141502Z-7c1e09ab")
    return RunId(value)


def parse_artifact_name(value: str) -> ArtifactName:
    """Validate an untrusted string as an artifact name."""
    if _ARTIFACT_NAME_RE.fullmatch(value) is None:
        raise ValueError(
            "an artifact name is 1 to 4 '/'-separated segments of letters, digits, '.', '_' "
            "and '-', each starting with a letter or digit"
        )
    return ArtifactName(value)


class RunStatus(StrEnum):
    """Where a run is in its lifecycle. Later phases add the waiting and review states."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepStatus(StrEnum):
    """What happened to one step."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_RUN = "not_run"


class ErrorCategory(StrEnum):
    """Whether a failure belongs to the workflow and the site, or to Mendwork's own machinery."""

    STEP = "step"
    INFRASTRUCTURE = "infrastructure"


class SelectorOutcome(StrEnum):
    """What one ranked selector found at Rung 0."""

    HIT = "hit"
    """Exactly one visible element at every scope level."""
    NONE = "none"
    """Some level matched no visible element."""
    MANY = "many"
    """Some level matched several visible elements."""


class TraceWithheldReason(StrEnum):
    """Why a failure's trace was not saved."""

    SECRET_BEARING_PAGE = "secret_bearing_page"  # noqa: S105 - a reason code, not a credential
    """The failing page still held a value typed from a secret, so its trace could contain it."""
    SECRET_DETECTED = "secret_detected"  # noqa: S105 - a reason code, not a credential
    """The trace was scanned, found to contain a secret value, and deleted."""


class SelectorReport(DomainModel):
    """One recorded selector's result at Rung 0."""

    rank: int = Field(ge=0)
    strategy: SelectorStrategy
    level_counts: tuple[int, ...]
    """Visible matches per scope level, outermost first, stopping at the first level that
    did not match exactly one element."""
    outcome: SelectorOutcome
    element: int | None = None
    """For a hit, which distinct element it found, numbered in order of first appearance."""


class IdentityReport(DomainModel):
    """An element's identity as the page reported it."""

    tag: str
    input_type: str | None = None
    role: str | None = None
    name: str
    confirmed: bool | None = None
    """Whether Playwright's own role locator agrees; None when there is no role to confirm."""


class TargetEvidence(DomainModel):
    """Everything Rung 0 observed while resolving a step's target."""

    selectors: tuple[SelectorReport, ...]
    resolved_rank: int | None = None
    """The best-ranked selector that hit, when the hits agreed."""
    identity: IdentityReport | None = None
    """The identity of the element the hits agreed on."""
    elements: tuple[IdentityReport, ...] = ()
    """One identity per distinct element, when the hits disagreed."""
    differences: tuple[str, ...] = ()
    """How the found identity differs from the recorded one, for a drifted match."""


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
    checkpoints: tuple[CheckpointResult, ...] = ()
    error: ErrorReport | None = None
    artifacts: StepArtifacts = StepArtifacts()


class Run(DomainModel):
    """One replay of one workflow version: the record saved as ``run.json``."""

    record_version: Literal[1] = 1
    run_id: RunIdField
    workflow_id: WorkflowIdField
    workflow_version: VersionNumber
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    inputs: Mapping[str, str] = Field(default_factory=dict)
    """Run inputs, including defaults applied. Secrets are listed by name only."""
    secrets: tuple[SecretNameField, ...] = ()
    steps: tuple[StepResult, ...] = ()
    """One result per step of the workflow, in order; steps after a failure are not_run."""
    error: ErrorReport | None = None

    @property
    def failed_step(self) -> StepResult | None:
        """The step the run stopped at, if it failed at one."""
        return next((step for step in self.steps if step.status is StepStatus.FAILED), None)
