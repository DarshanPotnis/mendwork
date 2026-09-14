"""The UsageLedger port: how many model calls a workspace made on a day.

The daily budget must hold across runs and processes, so the count lives outside any one run.
A ledger instance is bound to one workspace when it is constructed.
"""

from datetime import date
from typing import Protocol


class UsageLedger(Protocol):
    """A per-day count of model calls."""

    async def reserve_call(self, day: date, limit: int) -> int | None:
        """Count one call against ``day`` if fewer than ``limit`` are counted, atomically.

        Returns the new count, or None when the day's limit is already reached. Raises
        BudgetExceeded when the count cannot be read, so an unreadable ledger never allows a
        call.
        """
        ...
