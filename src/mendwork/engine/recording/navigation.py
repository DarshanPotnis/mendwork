"""Attributing main-frame navigations to steps.

A step is a window: from the observation before its action to the observation after the
page settled. A navigation committed inside a window belongs to that step, so a click that
navigates is one step. Outside any window:

- the browser started it (address bar, back, forward, reload): a NAVIGATE step of its own;
- the page started it (a meta refresh, a redirect, a script): not a step, because replaying
  the step before it makes the page do it again.

Inside a click or key window, a navigation the browser started is not something the step
caused, so the recording cannot continue.
"""

from collections.abc import Sequence
from enum import StrEnum

from mendwork.engine.ports.recording_types import NavigationInitiator, NavigationRecord


class OutsideNavigation(StrEnum):
    """What to do with a navigation that committed outside any step's window."""

    ALREADY_ATTRIBUTED = "already_attributed"
    STEP = "step"
    IGNORE = "ignore"


def classify_outside(record: NavigationRecord, attributed_through: int) -> OutsideNavigation:
    """Whether a navigation event is already part of a step, a step of its own, or ignored."""
    if record.sequence <= attributed_through:
        return OutsideNavigation.ALREADY_ATTRIBUTED
    if record.initiator is NavigationInitiator.BROWSER:
        return OutsideNavigation.STEP
    return OutsideNavigation.IGNORE


def browser_navigations(records: Sequence[NavigationRecord]) -> tuple[NavigationRecord, ...]:
    """The navigations in a window that the browser started, not the step's action."""
    return tuple(record for record in records if record.initiator is NavigationInitiator.BROWSER)
