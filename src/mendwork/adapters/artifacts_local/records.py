"""Run records in the local artifacts directory: claiming a run, and reading its record back.

A claim is an exclusive, non-blocking ``flock`` on ``runs/<run_id>/.lock``, held for as long as
the process runs or resumes the run. The operating system releases it when the process ends,
however it ends, so a record still marked running with no claim belongs to a process that is gone.
File locks need a POSIX system (Linux, macOS, or WSL on Windows).
"""

import asyncio
import fcntl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import BinaryIO, Final

from pydantic import ValidationError

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.engine.domain.runs import Run, RunId
from mendwork.engine.errors import ArtifactStoreUnavailable, RunBusy, UnknownRun
from mendwork.engine.replay.artifact_names import RUN_RECORD, WORKFLOW_SNAPSHOT

LOCK_NAME: Final = ".lock"


class LocalRunRecords:
    """RunRecords on a LocalArtifactStore's directory."""

    def __init__(self, store: LocalArtifactStore) -> None:
        self._store = store

    @asynccontextmanager
    async def claim(self, run_id: RunId) -> AsyncIterator[None]:
        handle = await asyncio.to_thread(self._acquire, run_id)
        try:
            yield
        finally:
            await asyncio.to_thread(_release, handle)

    async def is_claimed(self, run_id: RunId) -> bool:
        return await asyncio.to_thread(self._probe, run_id)

    async def load_run(self, run_id: RunId) -> Run:
        data = await self._read(run_id, RUN_RECORD)
        try:
            return Run.model_validate_json(data)
        except ValidationError as error:
            raise UnknownRun(f"the record of run {run_id} cannot be read", run_id=run_id) from error

    async def load_workflow(self, run_id: RunId) -> bytes:
        return await self._read(run_id, WORKFLOW_SNAPSHOT)

    async def _read(self, run_id: RunId, name: str) -> bytes:
        path = self._store.run_directory(run_id) / name
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise UnknownRun(f"no run {run_id} has a {name}", run_id=run_id) from None
        except OSError as error:
            raise ArtifactStoreUnavailable(
                "could not read a run record", run_id=run_id, name=name, errno=error.errno
            ) from error

    def _acquire(self, run_id: RunId) -> BinaryIO:
        directory = self._store.run_directory(run_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handle = (directory / LOCK_NAME).open("a+b")
        except OSError as error:
            raise ArtifactStoreUnavailable(
                "could not claim a run", run_id=run_id, errno=error.errno
            ) from error
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RunBusy(
                f"run {run_id} is being run or resumed by another process", run_id=run_id
            ) from None
        except OSError as error:
            handle.close()
            raise ArtifactStoreUnavailable(
                "could not claim a run", run_id=run_id, errno=error.errno
            ) from error
        return handle

    def _probe(self, run_id: RunId) -> bool:
        lock = self._store.run_directory(run_id) / LOCK_NAME
        try:
            handle = lock.open("rb")
        except FileNotFoundError:
            return False
        with handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False


def _release(handle: BinaryIO) -> None:
    with handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
