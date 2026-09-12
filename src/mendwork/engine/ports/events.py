"""The EventSink port: where a run's events go as they happen."""

from typing import Protocol

from mendwork.engine.domain.events import RunEvent


class EventSink(Protocol):
    """Receives every event of a run, in sequence order, already scrubbed of secrets."""

    async def emit(self, event: RunEvent) -> None:
        """Deliver one event."""
        ...
