"""Transient retries for navigation: which failures qualify, and how long to wait.

Retries are separate from healing and apply only to a navigate step's page load. A click
is never retried, because repeating an action is not safe, and resolution failures are
never retried, because a missing target is information rather than bad luck.
"""

from typing import Final

from mendwork.engine.replay.config import RetryPolicy

TIMEOUT_REASON: Final = "timeout"
HTTP_STATUS_REASON: Final = "http_status"

# Chromium network errors that describe a momentary condition. Certificate errors,
# NXDOMAIN, blocked requests, and aborted loads are deliberately absent: retrying them
# either cannot help or would paper over a security problem.
TRANSIENT_NETWORK_ERRORS: Final = frozenset(
    {
        "ERR_CONNECTION_CLOSED",
        "ERR_CONNECTION_REFUSED",
        "ERR_CONNECTION_RESET",
        "ERR_CONNECTION_TIMED_OUT",
        "ERR_EMPTY_RESPONSE",
        "ERR_INTERNET_DISCONNECTED",
        "ERR_NAME_RESOLUTION_FAILED",
        "ERR_NETWORK_CHANGED",
        "ERR_TIMED_OUT",
    }
)
# Gateway and availability errors; a 500 is a server bug and a 4xx will not change.
TRANSIENT_HTTP_STATUSES: Final = frozenset({502, 503, 504})


def is_transient(reason: object, status: object) -> bool:
    """Whether a navigation failure is worth another attempt."""
    if reason == TIMEOUT_REASON:
        return True
    if reason == HTTP_STATUS_REASON:
        return status in TRANSIENT_HTTP_STATUSES
    return reason in TRANSIENT_NETWORK_ERRORS


def backoff_delay_ms(failed_attempt: int, policy: RetryPolicy, unit: float) -> float:
    """The pause after a failed attempt (1-based), given a uniform random number in [0, 1).

    The base delay grows geometrically and is capped; jitter then removes up to
    ``jitter_ratio`` of it, so concurrent workers do not retry in lockstep while every
    delay stays at least ``(1 - jitter_ratio)`` of its base.
    """
    if failed_attempt < 1:
        raise ValueError("failed_attempt starts at 1")
    if not 0.0 <= unit < 1.0:
        raise ValueError("unit must be in [0, 1)")
    exponent = min(failed_attempt - 1, 64)
    base = min(float(policy.max_delay_ms), policy.initial_delay_ms * policy.multiplier**exponent)
    return base * (1.0 - policy.jitter_ratio * unit)
