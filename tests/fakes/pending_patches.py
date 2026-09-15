"""Pending patches in memory, with an exclusive hold per workflow like the file store's."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.patches import PendingPatch
from mendwork.engine.errors import WorkflowStoreUnavailable


class InMemoryHold:
    """A hold on one workflow's pending patches."""

    def __init__(self, store: "InMemoryPendingPatches", workflow_id: str) -> None:
        self._store = store
        self._workflow_id = workflow_id
        self._patches = store.documents.get(workflow_id, ())

    @property
    def patches(self) -> tuple[PendingPatch, ...]:
        return self._patches

    async def replace(self, patches: Sequence[PendingPatch]) -> None:
        self._store.log.append("replace")
        if self._store.fail_writes:
            raise WorkflowStoreUnavailable("pending patches cannot be written on purpose")
        self._store.documents[self._workflow_id] = tuple(patches)


class InMemoryPendingPatches:
    """Keeps each workflow's pending patches; ``log`` records every read, hold, and replace."""

    def __init__(
        self,
        *,
        fail_reads: bool = False,
        fail_writes: bool = False,
        log: list[str] | None = None,
    ) -> None:
        self.documents: dict[str, tuple[PendingPatch, ...]] = {}
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self.log: list[str] = log if log is not None else []
        self._locks: dict[str, asyncio.Lock] = {}

    async def read(self, workflow_id: WorkflowId) -> tuple[PendingPatch, ...]:
        self.log.append("read")
        if self.fail_reads:
            raise WorkflowStoreUnavailable("pending patches cannot be read on purpose")
        return self.documents.get(workflow_id, ())

    @asynccontextmanager
    async def hold(self, workflow_id: WorkflowId) -> AsyncIterator[InMemoryHold]:
        self.log.append("hold")
        if self.fail_reads:
            raise WorkflowStoreUnavailable("pending patches cannot be read on purpose")
        lock = self._locks.setdefault(workflow_id, asyncio.Lock())
        async with lock:
            yield InMemoryHold(self, workflow_id)
