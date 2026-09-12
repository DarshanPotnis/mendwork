"""The Timer port: elapsed time and deliberate pauses, so tests control both."""

from typing import Protocol


class Timer(Protocol):
    """Monotonic time for deadlines and durations, and the only way the engine waits on time.

    ``pause`` exists for retry backoff alone. Waiting for page state is never a pause: it
    is an explicit condition with a timeout, evaluated by the browser.
    """

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin that never goes backwards."""
        ...

    async def pause(self, seconds: float) -> None:
        """Wait before retrying a transient failure."""
        ...
