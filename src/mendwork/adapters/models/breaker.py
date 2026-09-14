"""A circuit breaker: stop calling a provider that keeps failing, and try it again after a pause.

Closed, calls go through. After ``failure_threshold`` failures in a row the breaker opens and
every call is refused at once with ``circuit_open``, so a broken provider costs no time per
heal. Once ``reset_ms`` has passed, one trial call is let through (half-open): success closes
the breaker, failure opens it again for another ``reset_ms``. A reply in the wrong shape is
not a provider failure; only calls that got no usable response count.
"""

import math
from enum import StrEnum

from mendwork.engine.errors import ProviderError
from mendwork.engine.ports.timer import Timer


class BreakerState(StrEnum):
    """Whether calls may go through."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """One provider's failure count and pause, measured on the Timer port."""

    def __init__(self, *, failure_threshold: int, reset_ms: int, timer: Timer) -> None:
        self._threshold = failure_threshold
        self._reset_ms = reset_ms
        self._timer = timer
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_running = False

    @property
    def state(self) -> BreakerState:
        """The breaker's state now."""
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._elapsed_ms(self._opened_at) < self._reset_ms:
            return BreakerState.OPEN
        return BreakerState.HALF_OPEN

    def before_call(self) -> None:
        """Let a call through, or raise ProviderError(reason=circuit_open)."""
        opened_at = self._opened_at
        if opened_at is None:
            return
        waited_ms = self._elapsed_ms(opened_at)
        if waited_ms < self._reset_ms:
            seconds = math.ceil((self._reset_ms - waited_ms) / 1000)
            raise ProviderError(
                f"calls to the provider are paused after {self._failures} failures in a row; "
                f"the next try is allowed in {seconds} s",
                reason="circuit_open",
                failures=self._failures,
                retry_in_ms=math.ceil(self._reset_ms - waited_ms),
            )
        if self._trial_running:
            raise ProviderError(
                "calls to the provider are paused while a trial call checks whether it recovered",
                reason="circuit_open",
                failures=self._failures,
                retry_in_ms=0,
            )
        self._trial_running = True

    def succeeded(self) -> None:
        """A call got a response: the provider works."""
        self._failures = 0
        self._opened_at = None
        self._trial_running = False

    def failed(self) -> None:
        """A call got no usable response."""
        self._failures += 1
        self._trial_running = False
        if self._opened_at is not None or self._failures >= self._threshold:
            self._opened_at = self._timer.monotonic()

    def _elapsed_ms(self, since: float) -> float:
        return (self._timer.monotonic() - since) * 1000
