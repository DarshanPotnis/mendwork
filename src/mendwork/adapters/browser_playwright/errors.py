"""Translating Playwright failures into engine errors, with only the context that is safe.

Playwright messages include call logs. Only the first line is kept as ``detail``, and the
engine scrubs it of secrets before recording it.
"""

import re
from typing import Final

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from mendwork.engine.errors import (
    BrowserUnavailable,
    MendworkError,
    NavigationError,
    TargetNotActionable,
    TargetNotFound,
)
from mendwork.engine.replay.retry import TIMEOUT_REASON

_NET_ERROR: Final = re.compile(r"net::(ERR_[A-Z0-9_]+)")
SOCKS_CONNECTION_FAILED: Final = "ERR_SOCKS_CONNECTION_FAILED"
"""How Chromium reports any connection the egress gateway did not complete, whatever the cause."""
_CLOSED: Final = (
    "Target page, context or browser has been closed",
    "Browser has been closed",
    "Target closed",
    "Connection closed",
)
_DETACHED: Final = (
    "not attached to the DOM",
    "Element is detached",
    "Execution context was destroyed",
)
_CONTEXT_DESTROYED: Final = (
    "Execution context was destroyed",
    "Cannot find context with specified id",
)


def first_line(error: PlaywrightError) -> str:
    """The first line of a Playwright message, without its call log."""
    return error.message.strip().splitlines()[0] if error.message.strip() else type(error).__name__


def is_closed(error: Exception) -> bool:
    """Whether the page, context, or browser is gone, or Playwright's driver with them.

    Playwright reports a driver that has already exited with a plain ``Exception`` ("Connection
    closed while reading from the driver"), not its own ``Error``, so both are read here.
    """
    message = error.message if isinstance(error, PlaywrightError) else str(error)
    return any(marker in message for marker in _CLOSED)


def is_context_destroyed(error: PlaywrightError) -> bool:
    """Whether the document an evaluation ran in was replaced by a navigation."""
    return any(marker in error.message for marker in _CONTEXT_DESTROYED)


def browser_closed(error: PlaywrightError) -> BrowserUnavailable:
    """The error for a browser that went away mid-run."""
    return BrowserUnavailable(
        "the browser closed while the run was using it", detail=first_line(error)
    )


def navigation_error(error: PlaywrightError, *, upstream_reason: str | None) -> MendworkError:
    """A failed page load, with a reason the retry policy can classify.

    Through the egress gateway, every failed connection reaches Chromium as
    ``ERR_SOCKS_CONNECTION_FAILED``. ``upstream_reason`` is what the gateway recorded (a refused
    or timed-out connect, a name that did not resolve), so retries classify the failure exactly
    as they would a direct connection's.
    """
    if is_closed(error):
        return browser_closed(error)
    if isinstance(error, PlaywrightTimeoutError):
        reason = TIMEOUT_REASON
    else:
        match = _NET_ERROR.search(error.message)
        reason = match.group(1) if match else "navigation_failed"
        if reason == SOCKS_CONNECTION_FAILED and upstream_reason is not None:
            reason = upstream_reason
    return NavigationError("the page could not be loaded", reason=reason, detail=first_line(error))


def action_error(error: PlaywrightError, action: str) -> MendworkError:
    """An action Playwright refused or could not complete. None of these performed it."""
    if is_closed(error):
        return browser_closed(error)
    if any(marker in error.message for marker in _DETACHED):
        return TargetNotFound(
            "the target was removed from the page before the action",
            reason="detached_before_action",
            action=action,
        )
    if isinstance(error, PlaywrightTimeoutError):
        return TargetNotActionable(
            f"the target did not become ready for the {action} action in time "
            "(covered, moving, disabled, or not editable)",
            reason="timeout",
            action=action,
            detail=first_line(error),
        )
    return TargetNotActionable(
        f"the browser refused the {action} action",
        reason="browser_refused",
        action=action,
        detail=first_line(error),
    )


def page_read_error(error: PlaywrightError) -> MendworkError:
    """The page could not be read, for a reason other than a navigation replacing it."""
    if is_closed(error):
        return browser_closed(error)
    return NavigationError(
        "the page could not be read", reason="page_unreadable", detail=first_line(error)
    )
