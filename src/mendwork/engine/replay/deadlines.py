"""Deadlines measured on the Timer port, so every wait is bounded and testable."""

import math
from dataclasses import dataclass

from mendwork.engine.ports.timer import Timer


@dataclass(frozen=True, slots=True)
class Deadline:
    """A moment on the timer's monotonic clock after which waiting must stop."""

    timer: Timer
    expires_at: float

    @classmethod
    def after(cls, timer: Timer, milliseconds: int) -> "Deadline":
        """A deadline ``milliseconds`` from now."""
        return cls(timer, timer.monotonic() + milliseconds / 1000)

    def earliest(self, other: "Deadline") -> "Deadline":
        """Whichever of two deadlines comes first."""
        return self if self.expires_at <= other.expires_at else other

    def remaining_ms(self) -> int:
        """Whole milliseconds left, never negative."""
        # Rounded to microseconds first: float subtraction leaves noise such as
        # 250.0000000000227, which a bare ceiling would turn into 251.
        left = round((self.expires_at - self.timer.monotonic()) * 1000, 3)
        return max(0, math.ceil(left))

    @property
    def expired(self) -> bool:
        """Whether no time is left."""
        return self.remaining_ms() == 0

    def timeout_ms(self) -> int:
        """The time left as a timeout, never below 1 (see ``cap``)."""
        return max(1, self.remaining_ms())

    def cap(self, milliseconds: int) -> int:
        """A timeout no longer than the time left, and never below 1.

        Browser libraries read a timeout of 0 as "wait forever", which would turn an
        expired deadline into an unbounded wait.
        """
        return max(1, min(milliseconds, self.remaining_ms()))
