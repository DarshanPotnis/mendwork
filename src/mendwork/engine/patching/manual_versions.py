"""Versions a person makes from the command line: importing a file, and rolling back (ADR 0013).

Each is planned, shown to the person, then published exactly as planned. A plan is a child of the
latest version when it was made, so when another process publishes a version in between, publishing
the plan fails with VersionConflict instead of replacing that version or building on one the person
was never shown. Nothing is deleted: an import is a new version carrying the file's content, and a
rollback a new version carrying an earlier version's.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.lineage import edit_version, roll_back_version
from mendwork.engine.domain.patches import PendingPatch
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import UnknownWorkflowVersion
from mendwork.engine.patching.diff import VersionDiff, diff_versions
from mendwork.engine.patching.history import (
    HistoryEntry,
    PendingLine,
    history_entries,
    pending_lines,
)
from mendwork.engine.patching.sources import first_version
from mendwork.engine.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class AlreadyLatest:
    """The file already has the latest stored version's content, so importing it changes nothing."""

    version: int


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """The version an import publishes, and what it changes."""

    version: WorkflowVersion
    latest: WorkflowVersion | None
    """The latest stored version, which ``version`` follows; None when the store has none."""
    diff: VersionDiff | None
    """From ``latest`` to ``version``."""
    stops_matching: tuple[PendingLine, ...]
    """Pending patches on the steps it changes: they will never become versions."""
    same_as: int | None
    """An earlier stored version with the file's content, which a rollback could restore instead."""


@dataclass(frozen=True, slots=True)
class RollbackPlan:
    """The version a rollback publishes, and what it undoes."""

    version: WorkflowVersion
    latest: WorkflowVersion
    restored: WorkflowVersion
    undone: tuple[HistoryEntry, ...]
    """Every version after the restored one, newest first, however recently it was published."""
    diff: VersionDiff
    """From ``latest`` to ``version``."""
    stops_matching: tuple[PendingLine, ...]
    undoes_heals: bool
    """Whether it undoes a heal, which is then never saved again automatically."""


def stored_version(
    workflow_id: WorkflowId, history: Sequence[WorkflowVersion], number: int
) -> WorkflowVersion:
    """One stored version. Raises UnknownWorkflowVersion when it is not in the history."""
    found = next((item for item in history if item.version == number), None)
    if found is not None:
        return found
    if not history:
        raise UnknownWorkflowVersion(
            f"the workflow store has no versions of {workflow_id}", workflow_id=workflow_id
        )
    raise UnknownWorkflowVersion(
        f"{workflow_id} has no v{number}; its versions are v1 to v{history[-1].version}",
        workflow_id=workflow_id,
        version=number,
    )


def plan_import(
    file: WorkflowVersion,
    history: Sequence[WorkflowVersion],
    pending: Sequence[PendingPatch],
    *,
    summary: str,
    clock: Clock,
) -> ImportPlan | AlreadyLatest:
    """What importing a file as its workflow's latest version would publish.

    Raises WorkflowValidationError when the file cannot follow the latest version, such as when its
    step ids differ from the latest version's.
    """
    if not history:
        return ImportPlan(
            version=first_version(file, clock),
            latest=None,
            diff=None,
            stops_matching=(),
            same_as=None,
        )
    latest = history[-1]
    if file.content == latest.content:
        return AlreadyLatest(version=latest.version)
    child = edit_version(latest, file.content, summary=summary, clock=clock)
    diff = diff_versions(latest, child)
    same_as = next(
        (item.version for item in reversed(history) if item.content == file.content), None
    )
    return ImportPlan(
        version=child,
        latest=latest,
        diff=diff,
        stops_matching=_stops_matching(diff, pending),
        same_as=same_as,
    )


def plan_rollback(
    workflow_id: WorkflowId,
    history: Sequence[WorkflowVersion],
    to: int,
    pending: Sequence[PendingPatch],
    *,
    reason: str,
    clock: Clock,
) -> RollbackPlan:
    """What rolling a workflow back to an earlier version would publish.

    The new version follows the latest version, not the version a person last looked at, and
    ``undone`` lists everything after the restored version, so a version published since a run was
    examined is shown as undone too. Raises UnknownWorkflowVersion when ``to`` is not stored, and
    WorkflowValidationError when it cannot be restored: it is the latest version, or the latest
    already has its content.
    """
    restored = stored_version(workflow_id, history, to)
    latest = history[-1]
    child = roll_back_version(latest, restored, reason=reason, clock=clock)
    later = [item for item in history if item.version > to]
    diff = diff_versions(latest, child)
    return RollbackPlan(
        version=child,
        latest=latest,
        restored=restored,
        undone=history_entries(later),
        diff=diff,
        stops_matching=_stops_matching(diff, pending),
        undoes_heals=any(isinstance(item.change, HealChange) for item in later),
    )


def _stops_matching(diff: VersionDiff, pending: Sequence[PendingPatch]) -> tuple[PendingLine, ...]:
    changed = {step.step_id for step in diff.steps}
    return tuple(line for line in pending_lines(diff.before, pending) if line.step_id in changed)
