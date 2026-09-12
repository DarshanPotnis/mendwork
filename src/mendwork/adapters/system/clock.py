"""The Clock port backed by the operating system's wall clock."""

from datetime import UTC, datetime


class SystemClock:
    """The current UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)
