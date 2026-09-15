"""The PendingPatches port: heals waiting for enough verified runs to become versions (ADR 0013).

Files now, one document per workflow; a table from Phase 10. A hold is exclusive per workflow, so
two runs finishing at once cannot both count the same success or both publish the same patch, and
everything one hold decides is stored by one replace. A store instance is bound to one tenant, like
the workflow store.
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.patches import PendingPatch


class PendingHold(Protocol):
    """One workflow's pending patches, held exclusively until the hold ends."""

    @property
    def patches(self) -> tuple[PendingPatch, ...]:
        """The patches as they were when the hold began."""
        ...

    async def replace(self, patches: Sequence[PendingPatch]) -> None:
        """Store these patches in place of every earlier one, atomically.

        Raises WorkflowStoreUnavailable when they cannot be stored.
        """
        ...


class PendingPatches(Protocol):
    """Where one tenant's pending patches live."""

    async def read(self, workflow_id: WorkflowId) -> tuple[PendingPatch, ...]:
        """The workflow's pending patches now. Raises WorkflowStoreUnavailable when unreadable."""
        ...

    def hold(self, workflow_id: WorkflowId) -> AbstractAsyncContextManager[PendingHold]:
        """Hold the workflow's pending patches exclusively. Raises WorkflowStoreUnavailable."""
        ...
