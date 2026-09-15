"""The AuditLog port as one file of JSON lines: ``<artifacts>/audit/audit.jsonl``.

An append takes an exclusive ``flock`` on the file, reads and checks the whole chain, writes the
new line with one ``write`` to a file opened for appending, and fsyncs before the lock is released,
so two decisions can never take the same place and a recorded decision survives a crash. A read
takes a shared lock and checks the chain too. The file is created readable by its owner only.

Anything that is not an unbroken chain (a line that is not an entry, an entry changed in place, a
line cut short by a crash mid-write) refuses every further read and append with AuditLogCorrupt: a
decision is never recorded against a log that cannot be trusted. File locks need a POSIX system.
"""

import asyncio
import fcntl
import os
from pathlib import Path
from typing import BinaryIO, Final

from pydantic import ValidationError

from mendwork.engine.domain.audit import (
    GENESIS_SHA256,
    AuditDraft,
    AuditEntry,
    chain_problem,
    chained,
)
from mendwork.engine.domain.runs import RunId
from mendwork.engine.errors import AuditLogCorrupt

LOG_NAME: Final = "audit.jsonl"
_PRIVATE: Final = 0o600


class FileAuditLog:
    """An AuditLog in one directory."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def path(self) -> Path:
        """The log file."""
        return self._directory / LOG_NAME

    async def append(self, draft: AuditDraft) -> AuditEntry:
        return await asyncio.to_thread(self._append, draft)

    async def entries_for_run(self, run_id: RunId) -> tuple[AuditEntry, ...]:
        entries = await asyncio.to_thread(self._read_all)
        return tuple(entry for entry in entries if entry.run_id == run_id)

    def _append(self, draft: AuditDraft) -> AuditEntry:
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT, _PRIVATE)
            with os.fdopen(descriptor, "r+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    entries = _entries(handle, self.path)
                    previous = entries[-1].sha256 if entries else GENESIS_SHA256
                    entry = chained(draft, sequence=len(entries) + 1, previous_sha256=previous)
                    handle.write(entry.model_dump_json().encode("utf-8") + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    return entry
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            raise AuditLogCorrupt(
                "the audit log could not be written", path=str(self.path), errno=error.errno
            ) from error

    def _read_all(self) -> tuple[AuditEntry, ...]:
        try:
            with self.path.open("rb") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                try:
                    return _entries(handle, self.path)
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except FileNotFoundError:
            return ()
        except OSError as error:
            raise AuditLogCorrupt(
                "the audit log could not be read", path=str(self.path), errno=error.errno
            ) from error


def _entries(handle: BinaryIO, path: Path) -> tuple[AuditEntry, ...]:
    handle.seek(0)
    data = handle.read()
    if data and not data.endswith(b"\n"):
        raise AuditLogCorrupt("the audit log ends with a line cut short", path=str(path))
    entries: list[AuditEntry] = []
    for number, line in enumerate(data.splitlines(), start=1):
        try:
            entries.append(AuditEntry.model_validate_json(line))
        except ValidationError as error:
            raise AuditLogCorrupt(
                f"line {number} of the audit log is not an audit entry", path=str(path)
            ) from error
    problem = chain_problem(entries)
    if problem is not None:
        raise AuditLogCorrupt(f"the audit log's chain is broken: {problem}", path=str(path))
    return tuple(entries)
