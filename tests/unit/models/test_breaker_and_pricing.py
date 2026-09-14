"""The circuit breaker pauses a failing provider; the price table estimates cost, never guesses."""

from decimal import Decimal

import pytest

from mendwork.adapters.models.breaker import BreakerState, CircuitBreaker
from mendwork.adapters.models.pricing import ModelPrice, estimated_cost
from mendwork.engine.errors import ProviderError
from tests.fakes.timer import FakeTimer


def breaker(timer: FakeTimer) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=3, reset_ms=60_000, timer=timer)


def test_failures_below_the_threshold_keep_calls_going() -> None:
    circuit = breaker(FakeTimer())

    circuit.failed()
    circuit.failed()
    circuit.before_call()

    assert circuit.state is BreakerState.CLOSED


def test_the_threshold_opens_the_breaker_and_calls_are_refused_with_the_wait() -> None:
    timer = FakeTimer()
    circuit = breaker(timer)
    for _ in range(3):
        circuit.failed()
    timer.advance_ms(20_000)

    with pytest.raises(ProviderError) as caught:
        circuit.before_call()

    assert circuit.state is BreakerState.OPEN
    assert caught.value.context == {"reason": "circuit_open", "failures": 3, "retry_in_ms": 40_000}
    assert caught.value.message == (
        "calls to the provider are paused after 3 failures in a row; the next try is allowed in "
        "40 s"
    )


def test_after_the_pause_one_trial_call_goes_through_and_its_success_closes_the_breaker() -> None:
    timer = FakeTimer()
    circuit = breaker(timer)
    for _ in range(3):
        circuit.failed()
    timer.advance_ms(60_000)

    circuit.before_call()
    during_trial = circuit.state
    with pytest.raises(ProviderError, match="trial call"):
        circuit.before_call()
    circuit.succeeded()
    after_success = circuit.state

    assert (during_trial, after_success) == (BreakerState.HALF_OPEN, BreakerState.CLOSED)
    circuit.before_call()


def test_a_failed_trial_opens_the_breaker_for_another_pause() -> None:
    timer = FakeTimer()
    circuit = breaker(timer)
    for _ in range(3):
        circuit.failed()
    timer.advance_ms(60_000)
    circuit.before_call()

    circuit.failed()

    assert circuit.state is BreakerState.OPEN
    with pytest.raises(ProviderError):
        circuit.before_call()


def test_a_success_resets_the_failure_count() -> None:
    circuit = breaker(FakeTimer())
    circuit.failed()
    circuit.failed()

    circuit.succeeded()
    circuit.failed()
    circuit.failed()

    assert circuit.state is BreakerState.CLOSED


PRICE = ModelPrice(
    input_usd_per_million_tokens=Decimal("0.10"), output_usd_per_million_tokens=Decimal("0.40")
)


def test_a_local_model_costs_nothing_whatever_it_reports() -> None:
    assert estimated_cost(local=True, price=None, input_tokens=None, output_tokens=None) == 0


def test_a_hosted_call_is_priced_from_the_table() -> None:
    cost = estimated_cost(local=False, price=PRICE, input_tokens=600, output_tokens=40)

    assert cost == Decimal("0.000076")


@pytest.mark.parametrize(
    ("price", "input_tokens", "output_tokens"),
    [(None, 600, 40), (PRICE, None, 40), (PRICE, 600, None)],
)
def test_a_call_whose_cost_cannot_be_known_is_unpriced_not_free(
    price: ModelPrice | None, input_tokens: int | None, output_tokens: int | None
) -> None:
    assert (
        estimated_cost(
            local=False, price=price, input_tokens=input_tokens, output_tokens=output_tokens
        )
        is None
    )
