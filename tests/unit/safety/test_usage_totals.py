"""A run's model usage totals are exactly the sum of its calls, and tokens a provider did not
report are counted as unreported, never as 0."""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.domain.model_evidence import ModelUsage, ModelUsageTotals


@given(
    st.lists(
        st.tuples(
            st.none() | st.integers(0, 10_000),
            st.none() | st.integers(0, 1_000),
            st.none() | st.decimals(0, 1, places=6),
        )
    )
)
def test_totals_are_the_sum_of_every_call(
    calls: list[tuple[int | None, int | None, Decimal | None]],
) -> None:
    totals = ModelUsageTotals()
    for input_tokens, output_tokens, cost in calls:
        totals = totals.plus(
            ModelUsage(
                provider="fake",
                model="scripted",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=10,
                http_attempts=1,
                estimated_cost_usd=cost,
            )
        )
    reported = [
        (sent, received) for sent, received, _ in calls if sent is not None and received is not None
    ]

    assert totals.calls == len(calls)
    assert totals.input_tokens == sum(sent for sent, _ in reported)
    assert totals.output_tokens == sum(received for _, received in reported)
    assert totals.unreported_token_calls == len(calls) - len(reported)
    assert totals.estimated_cost_usd == sum(
        (cost for _, _, cost in calls if cost is not None), Decimal(0)
    )
    assert totals.unpriced_calls == sum(1 for _, _, cost in calls if cost is None)


def test_combined_totals_add_every_count() -> None:
    first = ModelUsageTotals(
        calls=2,
        input_tokens=10,
        output_tokens=2,
        latency_ms=5,
        estimated_cost_usd=Decimal("0.1"),
        unpriced_calls=1,
        unreported_token_calls=1,
    )
    second = ModelUsageTotals(calls=1, input_tokens=4, output_tokens=1, latency_ms=3)

    assert first.combined(second) == ModelUsageTotals(
        calls=3,
        input_tokens=14,
        output_tokens=3,
        latency_ms=8,
        estimated_cost_usd=Decimal("0.1"),
        unpriced_calls=1,
        unreported_token_calls=1,
    )
