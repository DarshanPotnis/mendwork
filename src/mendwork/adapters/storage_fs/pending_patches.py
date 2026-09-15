"""Pending patches on the local file system, beside the workflow store (ADR 0013).

Layout: ``<store root>/.pending/<workflow_id>.json`` with ``<workflow_id>.lock`` next to it, both
created 0600. A hold takes an exclusive ``flock`` on the lock file and keeps it until the hold ends,
so two runs finishing at once decide one after the other. A replace writes a temporary file in the
same directory, fsyncs it, renames it over the document, and fsyncs the directory, so a reader only
ever sees a whole document and a crash leaves the previous one. The directory's name starts with a
dot, which no workflow id can, so it never collides with a workflow's version directory.

A document this store did not write, or one it cannot read, raises WorkflowStoreUnavailable: nothing
is counted or published on evidence that cannot be trusted. File locks need a POSIX system.
"""

import asyncio
import fcntl
import os
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from mendwork.engine.domain.identifiers import WorkflowId, parse_workflow_id
from mendwork.engine.domain.patches import PendingPatch, PendingPatchDocument
from mendwork.engine.errors import WorkflowStoreUnavailable

PENDING_DIRECTORY: Final = ".pending"


class FilePendingHold:
    """A hold on one workflow's pending patches, released when its context ends."""

    def __init__(
        self,
        store: "FilePendingPatches",
        workflow_id: WorkflowId,
        patches: tuple[PendingPatch, ...],
    ) -> None:
        self._store = store
        self._workflow_id = workflow_id
        self._patches = patches

    @property
    def patches(self) -> tuple[PendingPatch, ...]:
        return self._patches

    async def replace(self, patches: Sequence[PendingPatch]) -> None:
        await asyncio.to_thread(self._store.write_document, self._workflow_id, tuple(patches))
        self._patches = tuple(patches)


class FilePendingPatches:
    """One tenant's pending patches, under its workflow store's root directory."""

    def __init__(self, root: Path) -> None:
        self._directory = root / PENDING_DIRECTORY

    @property
    def directory(self) -> Path:
        """Where the documents and their locks are."""
        return self._directory

    def document(self, workflow_id: WorkflowId) -> Path:
        """The document holding a workflow's pending patches."""
        return self._directory / f"{parse_workflow_id(workflow_id)}.json"

    async def read(self, workflow_id: WorkflowId) -> tuple[PendingPatch, ...]:
        return await asyncio.to_thread(self.read_document, parse_workflow_id(workflow_id))

    @asynccontextmanager
    async def hold(self, workflow_id: WorkflowId) -> AsyncIterator[FilePendingHold]:
        slug = parse_workflow_id(workflow_id)
        descriptor = await asyncio.to_thread(self._lock, slug)
        try:
            patches = await asyncio.to_thread(self.read_document, slug)
            yield FilePendingHold(self, slug, patches)
        finally:
            await asyncio.to_thread(_unlock, descriptor)

    def read_document(self, workflow_id: WorkflowId) -> tuple[PendingPatch, ...]:
        """The workflow's pending patches, read synchronously."""
        path = self.document(workflow_id)
        try:
            if self._directory.is_symlink() or path.is_symlink():
                raise _unavailable(path, "is a symbolic link")
            data = path.read_bytes()
        except FileNotFoundError:
            return ()
        except OSError as error:
            raise _unavailable(path, error.strerror or type(error).__name__) from error
        try:
            document = PendingPatchDocument.model_validate_json(data)
        except ValidationError as error:
            raise _unavailable(path, "is not a document this store wrote") from error
        if document.workflow_id != workflow_id:
            raise _unavailable(path, f"holds the pending patches of {document.workflow_id}")
        return document.patches

    def write_document(self, workflow_id: WorkflowId, patches: tuple[PendingPatch, ...]) -> None:
        """Replace the workflow's pending patches atomically, synchronously."""
        path = self.document(workflow_id)
        data = PendingPatchDocument(workflow_id=workflow_id, patches=patches).model_dump_json(
            indent=2
        )
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                dir=self._directory, prefix=f".{workflow_id}.", suffix=".tmp"
            )
            try:
                with os.fdopen(descriptor, "wb") as file:
                    file.write((data + "\n").encode("utf-8"))
                    file.flush()
                    os.fsync(file.fileno())
                Path(temporary).replace(path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
            _sync_directory(self._directory)
        except OSError as error:
            raise _unavailable(path, error.strerror or type(error).__name__) from error

    def _lock(self, workflow_id: WorkflowId) -> int:
        path = self._directory / f"{workflow_id}.lock"
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise _unavailable(path, error.strerror or type(error).__name__) from error
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as error:
            os.close(descriptor)
            raise _unavailable(path, error.strerror or type(error).__name__) from error
        return descriptor


def _unlock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unavailable(path: Path, problem: str) -> WorkflowStoreUnavailable:
    return WorkflowStoreUnavailable(
        f"the pending patches {path} cannot be used: {problem}", path=str(path)
    )
