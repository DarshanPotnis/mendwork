"""The AuditLog port: an append-only record of the decisions people make on runs."""

from typing import Protocol

from mendwork.engine.domain.audit import AuditDraft, AuditEntry
from mendwork.engine.domain.runs import RunId


class AuditLog(Protocol):
    """Entries are only ever added, each chained to the one before it; none is changed or removed.

    Files now; a table whose role may only insert from Phase 10.
    """

    async def append(self, draft: AuditDraft) -> AuditEntry:
        """Record a decision after every other entry, durably, before returning.

        Raises AuditLogCorrupt when the log cannot be read or its chain is broken: a decision is
        never recorded on top of a log that cannot be trusted.
        """
        ...

    async def entries_for_run(self, run_id: RunId) -> tuple[AuditEntry, ...]:
        """Every entry about the run, oldest first, after checking the whole chain.

        Raises AuditLogCorrupt when the log cannot be read or its chain is broken.
        """
        ...
