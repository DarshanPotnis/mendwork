"""The Clock port: the engine's only source of the current time."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Supplies the current time, so tests can fix it and records stay reproducible."""

    def now(self) -> datetime:
        """The current moment as a timezone-aware UTC datetime."""
        ...
