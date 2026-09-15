"""Patch records: what came of a run's heals, and the heals still waiting to be saved (ADR 0013).

A run records where the version it executed came from and whether its heals may become versions;
for each verified heal, the element's fingerprint as the recorder would have recorded it; and, when
it finishes, what came of every heal. Under the ``after_n_successes`` policy a heal waits as a
pending patch, keyed to the exact step it was verified against rather than to a version number, so
any change to that step retires it and a change elsewhere does not.
"""

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import HealedRung
from mendwork.engine.domain.identifiers import StepIdField, VersionNumber, WorkflowIdField
from mendwork.engine.domain.run_identifiers import ArtifactNameField, RunIdField
from mendwork.engine.domain.steps import Step

SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
PENDING_ID_PATTERN: Final = r"^[0-9a-f]{16}$"
PENDING_DOCUMENT_VERSION: Final = 1


class CaptureProblem(StrEnum):
    """Why a healed element could not be fingerprinted, so no patch can come from its heal."""

    NO_SELECTOR = "no_selector"
    """No selector finds exactly this element and nothing else."""
    IDENTITY_UNCONFIRMED = "identity_unconfirmed"
    UNRECORDABLE_TARGET = "unrecordable_target"
    """Its name is too long, or holds characters a workflow cannot store."""
    ELEMENT_GONE = "element_gone"
    PAGE_NEVER_STABLE = "page_never_stable"
    SECRET_IN_TARGET = "secret_in_target"  # noqa: S105 - a reason code, not a credential
    """Some of its text holds a value the run resolved from a secret, so it is never stored."""


class ImageBox(DomainModel):
    """Where an element sits in a screenshot, as fractions of the image."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(ge=0.0, le=1.0)
    height: float = Field(ge=0.0, le=1.0)


class FoundTarget(DomainModel):
    """A heal's element as the recorder would record it: the target a new version would carry."""

    fingerprint: Fingerprint | None = None
    """Derived by the recorder's own selector derivation, and proven to resolve at Rung 0."""
    problem: CaptureProblem | None = None
    detail: str | None = None
    screenshot: ArtifactNameField | None = None
    """A masked screenshot around the element, taken just before the action."""
    box: ImageBox | None = None
    """Where the element sits in that screenshot."""

    @model_validator(mode="after")
    def _fingerprint_or_problem(self) -> Self:
        if (self.fingerprint is None) == (self.problem is None):
            raise ValueError("a found target has either a fingerprint or a problem")
        return self


class NotSavedReason(StrEnum):
    """Why a run's heals cannot become versions."""

    EXACT = "exact"
    """The file was run exactly as written (``--exact``)."""
    FILE_DIFFERS = "file_differs"
    """The file's content matches no stored version of its workflow."""
    NO_LINEAGE = "no_lineage"
    """The store holds nothing for the workflow, and the file is not a first version."""
    NEWER_IMPORT = "newer_import"
    """The file matches a stored version, but a later version was imported by hand."""
    STORE_UNAVAILABLE = "store_unavailable"


class WorkflowSource(DomainModel):
    """Where the version a run executes came from, and whether its heals may become versions."""

    path: str | None = None
    """The workflow file named on the command line."""
    stored_version: VersionNumber | None = None
    """The latest stored version whose content equals the file's."""
    ran_stored: bool = False
    """Whether a later stored version ran instead of the file as written."""
    saves_heals: bool
    not_saved: NotSavedReason | None = None

    @model_validator(mode="after")
    def _reason_when_not_saved(self) -> Self:
        if self.saves_heals == (self.not_saved is not None):
            raise ValueError("a source that does not save heals says why, and only then")
        return self


class PatchResult(StrEnum):
    """What came of one heal, or of one pending patch, when a run finished."""

    PUBLISHED = "published"
    """A new version carries the healed target."""
    PENDING = "pending"
    """Verified again, but not yet by as many runs as the promotion policy requires."""
    ALREADY_APPLIED = "already_applied"
    """The latest version already carries this target."""
    STALE = "stale"
    """The step changed since the run's version, so the heal no longer describes it."""
    PREVIOUSLY_ROLLED_BACK = "previously_rolled_back"
    """A person rolled back this exact heal; it is never saved again automatically."""
    NOT_CAPTURABLE = "not_capturable"
    NOT_APPROVED = "not_approved"
    """An irreversible step's heal without an approval that acted and was verified."""
    NOT_SAVED = "not_saved"
    """The run's source does not save heals."""
    RUN_NOT_SUCCEEDED = "run_not_succeeded"
    REFUSED = "refused"
    """The new version would not be a valid workflow."""
    CONFLICT = "conflict"
    """Other versions kept being published while this one was, until the attempts ran out."""
    STORE_UNAVAILABLE = "store_unavailable"
    DISCARDED = "discarded"
    """A pending patch was removed: the step changed, the page went back, or it failed."""


class PatchOutcome(DomainModel):
    """What came of one step's heal or pending patch."""

    step_id: StepIdField
    result: PatchResult
    version: VersionNumber | None = None
    """The version published, or the one that already carries the target or undid it."""
    rung: HealedRung | None = None
    strength: VerificationStrength | None = None
    successes: int | None = Field(default=None, ge=0)
    required: int | None = Field(default=None, ge=1)
    pending_id: str | None = Field(default=None, pattern=PENDING_ID_PATTERN)
    detail: str | None = None


class SuccessKind(StrEnum):
    """How a run verified a pending patch."""

    HEALED = "healed"
    """The ladder healed the step to the same element."""
    FIRST_TRY = "first_try"
    """The pending target resolved at Rung 0 and the step's checkpoints passed."""


class PatchSuccess(DomainModel):
    """One succeeded run that verified a pending patch."""

    run_id: RunIdField
    at: datetime
    how: SuccessKind


class PendingPatch(DomainModel):
    """A verified heal waiting for enough verified runs to become a version."""

    id: str = Field(pattern=PENDING_ID_PATTERN)
    workflow_id: WorkflowIdField
    step_id: StepIdField
    base_step_sha256: str = Field(pattern=SHA256_PATTERN)
    """The whole step the heal was verified against: target, checkpoints, risk, value, intent."""
    change: HealChange
    """The first verified heal's evidence."""
    successes: tuple[PatchSuccess, ...] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.change.step_id != self.step_id:
            raise ValueError("a pending patch's change is for its own step")
        runs = [success.run_id for success in self.successes]
        if len(set(runs)) != len(runs):
            raise ValueError("a run verifies a pending patch at most once")
        return self


class PendingPatchDocument(DomainModel):
    """Every pending patch of one workflow, as one stored document."""

    pending_version: Literal[1] = PENDING_DOCUMENT_VERSION
    workflow_id: WorkflowIdField
    patches: tuple[PendingPatch, ...] = ()


def step_digest(step: Step) -> str:
    """The SHA-256 of a step's canonical JSON: equal steps, and only equal steps, share it."""
    return _digest(step.model_dump(mode="json"))


def target_digest(fingerprint: Fingerprint) -> str:
    """The SHA-256 of a target's canonical JSON."""
    return _digest(fingerprint.model_dump(mode="json"))


def pending_id(step_id: str, base_step_sha256: str, new_target: Fingerprint) -> str:
    """The id of the pending patch that moves this exact step to this exact target."""
    key = f"{step_id}\n{base_step_sha256}\n{target_digest(new_target)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _digest(document: object) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()
