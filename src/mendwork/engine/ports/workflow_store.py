"""The WorkflowStore port: durable, append-only storage of workflow versions."""

from typing import Protocol

from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.workflow import WorkflowVersion


class WorkflowStore(Protocol):
    """Stores immutable workflow versions; a published version is never replaced.

    A store instance is bound to one tenant when it is constructed, so these methods take
    no workspace and no caller can query across tenants by passing the wrong one.
    """

    async def publish(self, version: WorkflowVersion) -> None:
        """Store a new version atomically.

        Raises VersionConflict if the number is already taken by different content or if
        its parent version is missing. Publishing byte-identical content again is a no-op,
        so retrying after an ambiguous failure is safe.
        """
        ...

    async def get(self, workflow_id: WorkflowId, version: int) -> WorkflowVersion | None:
        """One version, or None if it does not exist."""
        ...

    async def latest(self, workflow_id: WorkflowId) -> WorkflowVersion | None:
        """The highest-numbered version, or None if the workflow has none."""
        ...

    async def versions(self, workflow_id: WorkflowId) -> tuple[int, ...]:
        """Every stored version number, ascending."""
        ...
