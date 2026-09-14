"""Model-call budgets: per run in memory, per UTC day in a ledger, reserved before every call."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from mendwork.engine.domain.model_evidence import ModelUsage, ModelUsageTotals
from mendwork.engine.errors import BudgetExceeded
from mendwork.engine.safety.budgets import BudgetLimits, RunModelBudget, day_resets_at, usage_day
from tests.fakes.clock import FakeClock
from tests.fakes.ledger import InMemoryUsageLedger

pytestmark = pytest.mark.asyncio

LATE_EVENING = datetime(2026, 9, 13, 23, 30, tzinfo=UTC)


def usage(input_tokens: int = 100, cost: Decimal | None = Decimal(0)) -> ModelUsage:
    return ModelUsage(
        provider="fake",
        model="scripted",
        input_tokens=input_tokens,
        output_tokens=10,
        latency_ms=250,
        http_attempts=1,
        estimated_cost_usd=cost,
    )


def budget(
    per_run: int = 4, per_day: int = 200, ledger: InMemoryUsageLedger | None = None
) -> tuple[RunModelBudget, InMemoryUsageLedger]:
    used = ledger or InMemoryUsageLedger()
    limits = BudgetLimits(per_run=per_run, per_day=per_day)
    return RunModelBudget(limits=limits, ledger=used, clock=FakeClock(LATE_EVENING)), used


async def test_the_day_is_the_utc_date_and_resets_at_utc_midnight() -> None:
    eastern = timezone(timedelta(hours=-5))

    assert usage_day(datetime(2026, 9, 13, 20, 0, tzinfo=eastern)) == date(2026, 9, 14)
    assert day_resets_at(date(2026, 9, 13)) == datetime(2026, 9, 14, tzinfo=UTC)


async def test_a_run_reserves_up_to_its_limit_and_the_next_call_is_refused_before_the_ledger() -> (
    None
):
    run, ledger = budget(per_run=2)

    await run.reserve()
    await run.reserve()
    with pytest.raises(BudgetExceeded) as caught:
        await run.reserve()

    assert run.reserved == 2
    assert ledger.reservations == 2
    assert caught.value.context == {"scope": "run", "limit": 2, "used": 2}
    assert caught.value.message == "this run has used all 2 model calls it may make"


async def test_a_full_day_refuses_the_call_and_says_when_the_count_starts_again() -> None:
    run, ledger = budget(per_day=1)
    ledger.calls[date(2026, 9, 13)] = 1

    with pytest.raises(BudgetExceeded) as caught:
        await run.reserve()

    assert run.reserved == 0
    assert caught.value.context == {
        "scope": "day",
        "limit": 1,
        "day": "2026-09-13",
        "resets_at": "2026-09-14T00:00:00+00:00",
    }


async def test_a_run_limit_of_zero_allows_no_call() -> None:
    run, ledger = budget(per_run=0)

    with pytest.raises(BudgetExceeded):
        await run.reserve()

    assert ledger.reservations == 0


async def test_a_ledger_that_cannot_be_read_allows_no_call() -> None:
    failing = InMemoryUsageLedger(error=BudgetExceeded("unreadable", scope="day", limit=200))
    run, _ = budget(ledger=failing)

    with pytest.raises(BudgetExceeded, match="unreadable"):
        await run.reserve()

    assert run.reserved == 0


async def test_usage_is_added_up_and_an_unpriced_call_is_counted_not_guessed() -> None:
    run, _ = budget()

    run.record(usage(input_tokens=600, cost=Decimal("0.0003")))
    run.record(usage(input_tokens=400, cost=None))

    assert run.totals == ModelUsageTotals(
        calls=2,
        input_tokens=1_000,
        output_tokens=20,
        latency_ms=500,
        estimated_cost_usd=Decimal("0.0003"),
        unpriced_calls=1,
    )
