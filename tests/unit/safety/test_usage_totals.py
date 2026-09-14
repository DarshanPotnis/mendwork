"""A run's model usage totals are exactly the sum of its calls."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.domain.model_evidence import ModelUsage, ModelUsageTotals


@given(st.lists(st.tuples(st.integers(0, 10_000), st.none() | st.decimals(0, 1, places=6))))
def test_totals_are_the_sum_of_every_call(calls: list[tuple[int, Decimal | None]]) -> None:
    totals = ModelUsageTotals()
    for tokens, cost in calls:
        totals = totals.plus(
            ModelUsage(
                provider="fake",
                model="scripted",
                input_tokens=tokens,
                output_tokens=1,
                latency_ms=10,
                http_attempts=1,
                estimated_cost_usd=cost,
            )
        )

    assert totals.calls == len(calls)
    assert totals.input_tokens == sum(tokens for tokens, _ in calls)
    assert totals.output_tokens == len(calls)
    assert totals.estimated_cost_usd == sum(
        (cost for _, cost in calls if cost is not None), Decimal(0)
    )
    assert totals.unpriced_calls == sum(1 for _, cost in calls if cost is None)
