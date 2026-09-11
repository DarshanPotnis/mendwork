"""Creating child versions: the only way a workflow changes.

Each kind of change has its own function because each has different evidence to check.
A rollback's content must be the restored version's content, which a generic "parent plus
change record" function could not verify from the record alone. All of them share one
core that enforces the lineage rules: the next number, the parent untouched, and every
step id preserved in order.
"""

from pydantic import ValidationError

from mendwork.engine.domain.enums import ChangeKind
from mendwork.engine.domain.issues import issues_from_validation_error
from mendwork.engine.domain.workflow import (
    CURRENT_SCHEMA_VERSION,
    WorkflowContent,
    WorkflowVersion,
)
from mendwork.engine.errors import ValidationIssue, WorkflowValidationError
from mendwork.engine.ports.clock import Clock


def edit_version(
    parent: WorkflowVersion, content: WorkflowContent, *, summary: str, clock: Clock
) -> WorkflowVersion:
    """A child version carrying hand-edited content."""
    if content == parent.content:
        raise WorkflowValidationError(
            f"the edit to {parent.workflow_id} v{parent.version} changes nothing",
            workflow_id=parent.workflow_id,
            version=parent.version,
        )
    return _derive_child(
        parent, {"kind": ChangeKind.MANUAL_EDIT, "summary": summary}, content, clock
    )


def roll_back_version(
    parent: WorkflowVersion, restored: WorkflowVersion, *, reason: str, clock: Clock
) -> WorkflowVersion:
    """A child version carrying the content of an earlier version."""
    problem = _rollback_problem(parent, restored)
    if problem is not None:
        raise WorkflowValidationError(
            problem, workflow_id=parent.workflow_id, version=parent.version
        )
    change = {
        "kind": ChangeKind.ROLLBACK,
        "restored_version": restored.version,
        "reason": reason,
    }
    return _derive_child(parent, change, restored.content, clock)


def _rollback_problem(parent: WorkflowVersion, restored: WorkflowVersion) -> str | None:
    if restored.workflow_id != parent.workflow_id:
        return f"cannot restore {restored.workflow_id} into {parent.workflow_id}"
    if restored.version >= parent.version:
        return f"can only roll back to a version before v{parent.version}, not v{restored.version}"
    if restored.content == parent.content:
        return f"v{parent.version} already has the content of v{restored.version}"
    return None


def _derive_child(
    parent: WorkflowVersion,
    change: dict[str, object],
    content: WorkflowContent,
    clock: Clock,
) -> WorkflowVersion:
    parent_ids = tuple(step.id for step in parent.steps)
    child_ids = tuple(step.id for step in content.steps)
    if child_ids != parent_ids:
        raise WorkflowValidationError(
            "a new version must keep the same step ids in the same order",
            issues=(
                ValidationIssue(
                    ("steps",),
                    f"step ids must stay {list(parent_ids)}, got {list(child_ids)}",
                ),
            ),
            workflow_id=parent.workflow_id,
            version=parent.version,
        )
    candidate = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "workflow_id": parent.workflow_id,
        "version": parent.version + 1,
        "parent_version": parent.version,
        "created_at": clock.now(),
        "change": change,
        "inputs": content.inputs,
        "secrets": content.secrets,
        "steps": content.steps,
    }
    try:
        return WorkflowVersion.model_validate(candidate)
    except ValidationError as error:
        issues = issues_from_validation_error(error, candidate)
        raise WorkflowValidationError(
            f"cannot derive v{parent.version + 1} of {parent.workflow_id}",
            issues=issues,
            workflow_id=parent.workflow_id,
            version=parent.version,
        ) from error
