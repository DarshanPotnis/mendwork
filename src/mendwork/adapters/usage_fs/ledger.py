"""The daily model-call count in files, safe across concurrent runs and processes.

Each UTC day has one document, ``model-calls-YYYY-MM-DD.json``, holding
``{"ledger_version": 1, "day": ..., "calls": ...}``. A reservation takes an exclusive
``flock`` on ``.lock`` in the same directory, reads the day's count, and, if it is below the
limit, writes the new count to a temporary file, fsyncs it, and renames it over the old one
before the lock is released, so readers never see a partial document and no two reservations
count the same call. Old days are kept; each is a few dozen bytes.

The ledger fails closed: a directory that cannot be used, or a document that is not one this
ledger wrote, raises BudgetExceeded rather than allowing a call. File locks need a POSIX
system (Linux, macOS, or WSL on Windows).

A ledger is bound to one workspace by its directory. From Phase 10 a database table takes over
behind the same port.
"""

import asyncio
import fcntl
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Final

from mendwork.engine.domain.model_evidence import BudgetScope
from mendwork.engine.errors import BudgetExceeded

LEDGER_VERSION: Final = 1
LOCK_NAME: Final = ".lock"


class FileUsageLedger:
    """A UsageLedger in one directory."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        """Where the ledger's documents are."""
        return self._directory

    def document(self, day: date) -> Path:
        """The document that holds a day's count."""
        return self._directory / f"model-calls-{day.isoformat()}.json"

    async def reserve_call(self, day: date, limit: int) -> int | None:
        return await asyncio.to_thread(self._reserve, day, limit)

    def _reserve(self, day: date, limit: int) -> int | None:
        if limit <= 0:
            return None
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            with (self._directory / LOCK_NAME).open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    calls = self._read(day, limit)
                    if calls >= limit:
                        return None
                    self._write(day, calls + 1)
                    return calls + 1
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            raise self._unavailable(
                f"the model usage ledger in {self._directory} could not be used "
                f"({error.strerror or type(error).__name__})",
                limit,
            ) from error

    def _read(self, day: date, limit: int) -> int:
        path = self.document(day)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            document = None
        calls = document.get("calls") if isinstance(document, dict) else None
        valid = (
            isinstance(document, dict)
            and document.get("ledger_version") == LEDGER_VERSION
            and document.get("day") == day.isoformat()
            and isinstance(calls, int)
            and not isinstance(calls, bool)
            and calls >= 0
        )
        if not valid or not isinstance(calls, int):
            raise self._unavailable(
                f"the model usage ledger {path} is not a document this ledger wrote, so no call "
                "is allowed until it is fixed or removed",
                limit,
            )
        return calls

    def _write(self, day: date, calls: int) -> None:
        document = {"ledger_version": LEDGER_VERSION, "day": day.isoformat(), "calls": calls}
        data = (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(
            dir=self._directory, prefix=".model-calls-", suffix=".tmp"
        )
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(descriptor, view) :]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            Path(temporary).replace(self.document(day))
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise
        directory = os.open(self._directory, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _unavailable(self, message: str, limit: int) -> BudgetExceeded:
        return BudgetExceeded(
            message,
            scope=BudgetScope.DAY.value,
            limit=limit,
            reason="ledger_unavailable",
            path=str(self._directory),
        )
