"""The Timer port backed by the monotonic clock and asyncio."""

import asyncio
import time


class AsyncioTimer:
    """Monotonic time, and pauses that yield to the event loop.

    Pauses exist for retry backoff only; the engine never pauses to wait for a page.
    """

    def monotonic(self) -> float:
        return time.monotonic()

    async def pause(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
