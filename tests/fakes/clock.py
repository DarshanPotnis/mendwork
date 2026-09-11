"""A Clock that only moves when a test moves it."""

from datetime import datetime, timedelta


class FakeClock:
    """Returns a fixed UTC time until advanced, so timestamps in tests are exact."""

    def __init__(self, start: datetime) -> None:
        if start.utcoffset() is None:
            raise ValueError("FakeClock needs a timezone-aware start time")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
