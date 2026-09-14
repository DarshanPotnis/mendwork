"""An in-memory UsageLedger: a count per day, and an error to raise when a test wants one."""

from datetime import date

from mendwork.engine.errors import MendworkError


class InMemoryUsageLedger:
    """Counts reserved model calls per day, like the file ledger, without files."""

    def __init__(self, *, error: MendworkError | None = None) -> None:
        self.calls: dict[date, int] = {}
        self.error = error
        self.reservations = 0

    async def reserve_call(self, day: date, limit: int) -> int | None:
        self.reservations += 1
        if self.error is not None:
            raise self.error
        used = self.calls.get(day, 0)
        if used >= limit:
            return None
        self.calls[day] = used + 1
        return used + 1
