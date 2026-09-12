"""Waiting for a page condition without sleeping.

The condition is checked, and if it does not hold, the wait is for the page to change, not
for time to pass. The epoch is read before the check, so a change that lands while the
check runs wakes the wait at once rather than being missed.
"""

from collections.abc import Awaitable, Callable

from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.replay.deadlines import Deadline


async def wait_for_condition(
    browser: BrowserPort, deadline: Deadline, condition: Callable[[], Awaitable[bool]]
) -> bool:
    """Whether the condition held before the deadline. It is always checked at least once."""
    while True:
        epoch = await browser.dom_epoch(timeout_ms=deadline.timeout_ms())
        if await condition():
            return True
        if deadline.expired:
            return False
        await browser.wait_for_dom_change(epoch, timeout_ms=deadline.timeout_ms())
