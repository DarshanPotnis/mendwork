"""The RunRecords port: claiming a run for one process, and reading its record back."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from mendwork.engine.domain.runs import Run, RunId


class RunRecords(Protocol):
    """Exactly one process runs or resumes a run at a time, and its records can be read back.

    A claim lasts until the context exits or the process ends, however it ends, so a record still
    marked running that no process has claimed belongs to a process that is gone.
    """

    def claim(self, run_id: RunId) -> AbstractAsyncContextManager[None]:
        """Hold the run for this process. Raises RunBusy when another process holds it."""
        ...

    async def is_claimed(self, run_id: RunId) -> bool:
        """Whether some process holds the run now."""
        ...

    async def load_run(self, run_id: RunId) -> Run:
        """The run's record. Raises UnknownRun when there is none, or it cannot be read."""
        ...

    async def load_workflow(self, run_id: RunId) -> bytes:
        """The bytes of the workflow snapshot the run executes. Raises UnknownRun when missing."""
        ...
