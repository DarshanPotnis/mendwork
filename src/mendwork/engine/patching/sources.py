"""Which version a workflow file runs, and whether its heals become versions (ADR 0013).

A file given to ``mendwork run`` is never written. It is matched to its workflow's stored versions
by content, the decoded model, so comments and formatting never count as an edit, and a file
edited by hand while it still says ``version: 1`` is never taken for version 1:

- the store holds nothing for the workflow: a first version is published unchanged and run, and its
  heals are saved; any other file runs as written, and its heals are not saved;
- the file's content is a stored version's, and every later version came from a heal or a rollback:
  the latest version runs, and its heals are saved;
- the file's content is a stored version's, but a later version was imported by hand: the file runs
  as written, and nothing is saved, because the person pointed at an older file on purpose;
- no stored version has the file's content: the file runs as written, and nothing is saved until
  ``mendwork import`` makes it a version.

A store that cannot be read or written never stops a run: the file runs as written, saving nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import structlog

from mendwork.engine.domain.changes import ManualEdit
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.patches import NotSavedReason, WorkflowSource
from mendwork.engine.domain.workflow import CURRENT_SCHEMA_VERSION, WorkflowVersion
from mendwork.engine.errors import (
    InfrastructureError,
    PolicyViolation,
    VersionConflict,
    WorkflowValidationError,
)
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.workflow_store import WorkflowStore

STORE_ERRORS: Final = (InfrastructureError, PolicyViolation, WorkflowValidationError)
"""What a store raises when it cannot be read or written, or holds a file it cannot read."""
_FIRST_PUBLISH_ATTEMPTS: Final = 2
"""A conflicting first version was stored by another process; one more read settles it."""


@dataclass(frozen=True, slots=True)
class SourceDecision:
    """The version a file's run executes, what the run records about it, and what to store first."""

    version: WorkflowVersion
    source: WorkflowSource
    publish_first: bool
    """Publish ``version`` as the workflow's first stored version before the run starts."""


def choose_version(
    file: WorkflowVersion, path: str, stored: Sequence[WorkflowVersion], *, exact: bool
) -> SourceDecision:
    """What a run of the file executes, given every stored version of its workflow in order."""
    if exact:
        return _as_written(file, path, NotSavedReason.EXACT)
    if not stored:
        if file.version == 1 and file.change is None:
            source = WorkflowSource(path=path, stored_version=1, saves_heals=True)
            return SourceDecision(version=file, source=source, publish_first=True)
        return _as_written(file, path, NotSavedReason.NO_LINEAGE)
    matching = [version for version in stored if version.content == file.content]
    if not matching:
        return _as_written(file, path, NotSavedReason.FILE_DIFFERS)
    match = matching[-1]
    later = [version for version in stored if version.version > match.version]
    if any(isinstance(version.change, ManualEdit) for version in later):
        return _as_written(file, path, NotSavedReason.NEWER_IMPORT, stored_version=match.version)
    latest = stored[-1]
    source = WorkflowSource(
        path=path,
        stored_version=match.version,
        ran_stored=latest != file,
        saves_heals=True,
    )
    return SourceDecision(version=latest, source=source, publish_first=False)


def first_version(file: WorkflowVersion, clock: Clock) -> WorkflowVersion:
    """A file's content as a workflow's first version, whatever number the file itself carries."""
    if file.version == 1 and file.change is None:
        return file
    return WorkflowVersion(
        schema_version=CURRENT_SCHEMA_VERSION,
        workflow_id=file.workflow_id,
        version=1,
        created_at=clock.now(),
        inputs=file.inputs,
        secrets=file.secrets,
        steps=file.steps,
    )


async def stored_history(
    store: WorkflowStore, workflow_id: WorkflowId
) -> tuple[WorkflowVersion, ...]:
    """Every stored version of a workflow, oldest first."""
    history: list[WorkflowVersion] = []
    for number in await store.versions(workflow_id):
        version = await store.get(workflow_id, number)
        if version is not None:
            history.append(version)
    return tuple(history)


async def resolve_source(
    store: WorkflowStore, file: WorkflowVersion, path: str, *, exact: bool
) -> SourceDecision:
    """What a run of the file executes, with a first version it calls for already published.

    ``publish_first`` in the result means the file was just stored as version 1. When another
    process stores a first version at the same moment, the choice is made again from what that
    process stored. A store that cannot be read or written never stops a run: the file runs as
    written, and its heals are not saved.
    """
    if exact:
        return choose_version(file, path, (), exact=True)
    try:
        for _ in range(_FIRST_PUBLISH_ATTEMPTS):
            history = await stored_history(store, file.workflow_id)
            decision = choose_version(file, path, history, exact=False)
            if not decision.publish_first:
                return decision
            try:
                await store.publish(decision.version)
            except VersionConflict:
                continue
            return decision
        problem = "a first version conflicted with one the store does not list"
    except STORE_ERRORS as error:
        problem = type(error).__name__
    structlog.stdlib.get_logger("mendwork.patching").warning(
        "workflow_store_unusable", workflow_id=file.workflow_id, problem=problem
    )
    return _as_written(file, path, NotSavedReason.STORE_UNAVAILABLE)


def _as_written(
    file: WorkflowVersion, path: str, reason: NotSavedReason, *, stored_version: int | None = None
) -> SourceDecision:
    source = WorkflowSource(
        path=path, stored_version=stored_version, saves_heals=False, not_saved=reason
    )
    return SourceDecision(version=file, source=source, publish_first=False)
