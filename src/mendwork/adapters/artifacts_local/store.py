"""The ArtifactStore port on a local directory: ``<root>/runs/<run_id>/<name>``.

Writes are atomic (temporary file, fsync, rename), so a crash never leaves a half-written
``run.json`` that looks complete. Names are validated and resolved paths must stay inside
their run's directory, so a name can never write anywhere else.

Writes to one file land in the order they were asked for, even though each runs on a worker
thread: every request takes the next number for its path, and a write older than the one already
on disk is dropped. A run interrupted mid-write therefore can never have its final record
replaced by an earlier one that was still on its way. ``write_now`` and ``read_now`` do the same
synchronously, for an abort that cannot wait for the event loop.
"""

import asyncio
import errno
import os
import tempfile
import threading
from pathlib import Path

from mendwork.engine.domain.runs import ArtifactName, RunId, parse_artifact_name, parse_run_id
from mendwork.engine.errors import ArtifactStoreUnavailable


class LocalArtifactStore:
    """Run artifacts under one root directory."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._guard = threading.Lock()
        self._locks: dict[Path, threading.Lock] = {}
        self._issued: dict[Path, int] = {}
        self._written: dict[Path, int] = {}

    @property
    def root(self) -> Path:
        """The artifacts directory."""
        return self._root

    @property
    def runs_root(self) -> Path:
        """The directory holding one subdirectory per run."""
        return self._root / "runs"

    def run_directory(self, run_id: RunId) -> Path:
        """Where one run's artifacts live."""
        return self.runs_root / parse_run_id(run_id)

    async def write(self, run_id: RunId, name: ArtifactName, data: bytes) -> ArtifactName:
        path = self._path(run_id, name)
        order = self._issue(path)
        try:
            await asyncio.to_thread(self._write_in_order, path, data, order)
        except OSError as error:
            raise ArtifactStoreUnavailable(
                "could not write a run artifact", run_id=run_id, name=name, errno=error.errno
            ) from error
        return name

    def write_now(self, run_id: RunId, name: ArtifactName, data: bytes) -> ArtifactName:
        """Write synchronously, after any write of the same file already on its way."""
        path = self._path(run_id, name)
        try:
            self._write_in_order(path, data, self._issue(path))
        except OSError as error:
            raise ArtifactStoreUnavailable(
                "could not write a run artifact", run_id=run_id, name=name, errno=error.errno
            ) from error
        return name

    def read_now(self, run_id: RunId, name: ArtifactName) -> bytes | None:
        """A file's bytes, read synchronously once no write of it is in progress; None if absent."""
        path = self._path(run_id, name)
        with self._lock_for(path):
            try:
                return path.read_bytes()
            except FileNotFoundError:
                return None
            except OSError as error:
                raise ArtifactStoreUnavailable(
                    "could not read a run artifact", run_id=run_id, name=name, errno=error.errno
                ) from error

    async def adopt(self, run_id: RunId, name: ArtifactName, source: Path) -> ArtifactName:
        path = self._path(run_id, name)
        try:
            await asyncio.to_thread(_move, source, path)
        except OSError as error:
            raise ArtifactStoreUnavailable(
                "could not store a run artifact", run_id=run_id, name=name, errno=error.errno
            ) from error
        return name

    def _issue(self, path: Path) -> int:
        with self._guard:
            order = self._issued.get(path, 0) + 1
            self._issued[path] = order
            return order

    def _lock_for(self, path: Path) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(path, threading.Lock())

    def _write_in_order(self, path: Path, data: bytes, order: int) -> None:
        with self._lock_for(path):
            if self._written.get(path, 0) > order:
                return
            _atomic_write(path, data)
            self._written[path] = order

    def _path(self, run_id: RunId, name: ArtifactName) -> Path:
        directory = self.run_directory(run_id)
        path = directory / parse_artifact_name(name)
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ArtifactStoreUnavailable(
                "an artifact path leaves its run directory", run_id=run_id, name=name
            )
        return path


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _move(source: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.replace(path)
    except OSError as error:
        # Browser temp files may live on another file system, where rename is impossible.
        if error.errno != errno.EXDEV:
            raise
        _atomic_write(path, source.read_bytes())
        source.unlink()
        return
    # A file the browser created may be readable by other users; evidence is private.
    path.chmod(0o600)
