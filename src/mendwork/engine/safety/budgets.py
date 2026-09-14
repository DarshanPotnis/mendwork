"""Model-call budgets: at most so many calls per run and per workspace per day.

A call is counted when it is reserved, before it is made, so a crash never under-counts and a
call that fails still counts (a provider may bill it, and counting it stops a retry storm). A
repair call is a call. The run's count is checked first, in memory; the day's count is kept
by a UsageLedger, keyed by the UTC date from the Clock port, and an unreadable ledger allows
no call. A budget that is used up raises BudgetExceeded, and the step abstains.
"""

from datetime import UTC, date, datetime, time, timedelta

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.model_evidence import BudgetScope, ModelUsage, ModelUsageTotals
from mendwork.engine.errors import BudgetExceeded
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.usage_ledger import UsageLedger


class BudgetLimits(DomainModel):
    """How many model calls a run, and a workspace in a day, may make."""

    per_run: int = Field(ge=0)
    per_day: int = Field(ge=0)


def usage_day(now: datetime) -> date:
    """The UTC day a moment counts against."""
    return now.astimezone(UTC).date()


def day_resets_at(day: date) -> datetime:
    """When the next day's count begins."""
    return datetime.combine(day + timedelta(days=1), time(0), tzinfo=UTC)


class RunModelBudget:
    """One run's model calls: its own count, the day's ledger, and the usage it added up."""

    def __init__(self, *, limits: BudgetLimits, ledger: UsageLedger, clock: Clock) -> None:
        self._limits = limits
        self._ledger = ledger
        self._clock = clock
        self._reserved = 0
        self._totals = ModelUsageTotals()

    @property
    def reserved(self) -> int:
        """How many calls this run has reserved."""
        return self._reserved

    @property
    def totals(self) -> ModelUsageTotals:
        """The usage of every call recorded so far."""
        return self._totals

    async def reserve(self) -> None:
        """Count one call, or raise BudgetExceeded when either budget is used up."""
        per_run = self._limits.per_run
        if self._reserved >= per_run:
            raise BudgetExceeded(
                f"this run has used all {per_run} model call{_plural(per_run)} it may make",
                scope=BudgetScope.RUN.value,
                limit=per_run,
                used=self._reserved,
            )
        day = usage_day(self._clock.now())
        per_day = self._limits.per_day
        counted = await self._ledger.reserve_call(day, per_day)
        if counted is None:
            raise BudgetExceeded(
                f"the {per_day} model call{_plural(per_day)} allowed on {day.isoformat()} "
                "are used up",
                scope=BudgetScope.DAY.value,
                limit=per_day,
                day=day.isoformat(),
                resets_at=day_resets_at(day).isoformat(),
            )
        self._reserved += 1

    def record(self, usage: ModelUsage) -> None:
        """Add one call's usage to the run's totals."""
        self._totals = self._totals.plus(usage)


def _plural(count: int) -> str:
    return "" if count == 1 else "s"
