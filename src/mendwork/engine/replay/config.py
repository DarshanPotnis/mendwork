"""Replay tuning, handed to the engine by the app that builds it from Settings.

There are no defaults here: Settings is the only place a default may live, so a value the
engine uses is always one an operator can see and change.
"""

from pydantic import Field

from mendwork.engine.domain.base import DomainModel

Milliseconds = int


class RetryPolicy(DomainModel):
    """Bounded exponential backoff with jitter, for transient navigation failures only."""

    max_attempts: int = Field(ge=1, le=10)
    """Total attempts, the first one included."""
    initial_delay_ms: Milliseconds = Field(ge=0)
    max_delay_ms: Milliseconds = Field(ge=0)
    multiplier: float = Field(ge=1.0, le=10.0)
    jitter_ratio: float = Field(ge=0.0, le=1.0)
    """Each delay is reduced by up to this fraction, chosen at random."""


class ReplayConfig(DomainModel):
    """Every timeout and threshold replay uses."""

    step_timeout_ms: Milliseconds = Field(ge=1)
    """Settle, Rung 0 resolution, pre-action waits, and the action itself."""
    checkpoint_timeout_ms: Milliseconds = Field(ge=1)
    """A waiting checkpoint without its own timeout_ms."""
    navigation_timeout_ms: Milliseconds = Field(ge=1)
    """One attempt of a navigate step."""
    run_timeout_ms: Milliseconds = Field(ge=1)
    settle_timeout_ms: Milliseconds = Field(ge=1)
    """How long to wait for a quiet DOM before evaluating selectors on a busy one."""
    settle_quiet_frames: int = Field(ge=1, le=60)
    retry: RetryPolicy
