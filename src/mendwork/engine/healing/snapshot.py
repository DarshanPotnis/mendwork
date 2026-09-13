"""Reading a page consistently: settle, read, and keep the reading only if the DOM held still.

Healing compares many elements at once, so a reading the page changed under could pair one
element's name with another's position. Such a reading is released and taken again, until
the deadline; a page that changes under every reading cannot be healed safely.
"""

from collections.abc import Awaitable, Callable

from mendwork.engine.errors import PageNeverStable
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.replay.deadlines import Deadline


async def read_consistently[T](
    browser: BrowserPort,
    deadline: Deadline,
    *,
    settle_timeout_ms: int,
    quiet_frames: int,
    read: Callable[[], Awaitable[T]],
    release: Callable[[T], Awaitable[None]],
) -> T:
    """The first reading taken while the DOM did not change, or PageNeverStable."""
    while True:
        settling = await browser.wait_until_settled(
            quiet_frames=quiet_frames, timeout_ms=deadline.cap(settle_timeout_ms)
        )
        reading = await read()
        epoch = await browser.dom_epoch(timeout_ms=deadline.timeout_ms())
        if epoch == settling.epoch:
            return reading
        await release(reading)
        if deadline.expired:
            raise PageNeverStable(
                "the page kept changing while it was examined, so no heal could be chosen safely",
                reason="dom_changed_during_every_heal_reading",
            )
