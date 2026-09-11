"""Workflow versions: immutable, numbered, and each one explaining why it exists."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.changes import ChangeRecord, Rollback
from mendwork.engine.domain.identifiers import SecretNameField, VersionNumber, WorkflowIdField
from mendwork.engine.domain.limits import DECLARATIONS_MAX_ITEMS, STEPS_MAX_ITEMS
from mendwork.engine.domain.references import find_reference_issues
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.values import InputDeclaration
from mendwork.engine.errors import ValidationIssue

CURRENT_SCHEMA_VERSION: Final = 1
# The error type that carries workflow-wide issues, each with its own path, out of
# pydantic validation; see mendwork.engine.domain.issues.
REFERENCE_ERROR_TYPE: Final = "workflow_references"

Inputs = Annotated[tuple[InputDeclaration, ...], Field(max_length=DECLARATIONS_MAX_ITEMS)]
Secrets = Annotated[tuple[SecretNameField, ...], Field(max_length=DECLARATIONS_MAX_ITEMS)]
Steps = Annotated[tuple[Step, ...], Field(min_length=1, max_length=STEPS_MAX_ITEMS)]


def _raise_reference_issues(issues: tuple[ValidationIssue, ...]) -> None:
    if issues:
        summary = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        raise PydanticCustomError(
            REFERENCE_ERROR_TYPE, "{summary}", {"summary": summary, "issues": issues}
        )


class WorkflowContent(DomainModel):
    """What a version carries: its declarations and steps, without its lineage."""

    inputs: Inputs = ()
    secrets: Secrets = ()
    steps: Steps

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        _raise_reference_issues(find_reference_issues(self.inputs, self.secrets, self.steps))
        return self


class WorkflowVersion(DomainModel):
    """One immutable version of a workflow, as stored in one file."""

    schema_version: Literal[1]
    """The version of this file format; Mendwork refuses formats it does not know."""
    workflow_id: WorkflowIdField
    version: VersionNumber
    parent_version: VersionNumber | None = Field(default=None, validate_default=True)
    """The version this one was derived from: always the previous number, none for version 1."""
    created_at: datetime
    """When the version was created, in UTC."""
    change: ChangeRecord | None = Field(default=None, validate_default=True)
    """Why this version exists; none for version 1."""
    inputs: Inputs = ()
    """Run inputs the steps use. Their values are recorded in run history."""
    secrets: Secrets = ()
    """Names of secrets the steps use. Values are resolved only at the moment of use."""
    steps: Steps

    @field_validator("created_at")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("must be a UTC timestamp, such as 2026-09-11T08:30:00Z")
        return value.replace(tzinfo=UTC)

    @field_validator("parent_version")
    @classmethod
    def _parent_is_previous(cls, parent: int | None, info: ValidationInfo) -> int | None:
        version = info.data.get("version")
        if not isinstance(version, int):
            return parent
        if version == 1 and parent is not None:
            raise ValueError("version 1 has no parent; remove parent_version")
        if version > 1 and parent != version - 1:
            raise ValueError(f"must be {version - 1}: a version's parent is the version before it")
        return parent

    @field_validator("change")
    @classmethod
    def _change_explains_version(
        cls, change: ChangeRecord | None, info: ValidationInfo
    ) -> ChangeRecord | None:
        version = info.data.get("version")
        if not isinstance(version, int):
            return change
        if version == 1 and change is not None:
            raise ValueError("version 1 has no change record; remove change")
        if version > 1 and change is None:
            raise ValueError("is required: every version after the first records why it exists")
        if isinstance(change, Rollback) and change.restored_version > version - 2:
            raise ValueError(
                f"a rollback in version {version} can restore at most version {version - 2}; "
                "restoring the parent itself would change nothing"
            )
        return change

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        _raise_reference_issues(find_reference_issues(self.inputs, self.secrets, self.steps))
        return self

    @property
    def content(self) -> WorkflowContent:
        """The declarations and steps, for comparing or carrying content between versions."""
        return WorkflowContent(inputs=self.inputs, secrets=self.secrets, steps=self.steps)
