"""An in-memory AuditLog, chained exactly as the file log chains its entries."""

from mendwork.engine.domain.audit import GENESIS_SHA256, AuditDraft, AuditEntry, chained
from mendwork.engine.domain.runs import RunId
from mendwork.engine.errors import AuditLogCorrupt


class InMemoryAuditLog:
    """Entries in a list; ``corrupt`` makes every call fail as a broken log does."""

    def __init__(self, *, corrupt: bool = False) -> None:
        self.entries: list[AuditEntry] = []
        self.corrupt = corrupt

    async def append(self, draft: AuditDraft) -> AuditEntry:
        self._check()
        previous = self.entries[-1].sha256 if self.entries else GENESIS_SHA256
        entry = chained(draft, sequence=len(self.entries) + 1, previous_sha256=previous)
        self.entries.append(entry)
        return entry

    async def entries_for_run(self, run_id: RunId) -> tuple[AuditEntry, ...]:
        self._check()
        return tuple(entry for entry in self.entries if entry.run_id == run_id)

    def _check(self) -> None:
        if self.corrupt:
            raise AuditLogCorrupt("the audit log is broken on purpose", path="memory")
