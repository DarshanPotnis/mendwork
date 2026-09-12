"""Pre-action checks: can the verified element receive this step's action?

Some problems resolve themselves (a button enabled once the form is valid), so they are
waited on within the step's time. Others never will (a checkbox cannot be filled, a
button is not a select), so they fail at once.
"""

from dataclasses import dataclass
from typing import Final

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.errors import TargetDrifted, TargetNotActionable, TargetNotFound
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import Actionability, ElementIdentity, ElementRef
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.identity import effective_type, same_identity
from mendwork.engine.replay.reports import identity_report
from mendwork.engine.safety.secret_scrub import SecretScrubber

# Input types whose value cannot be typed; everything else (text, date, color, range…) can.
NON_FILLABLE_INPUT_TYPES: Final = frozenset(
    {"button", "checkbox", "file", "hidden", "image", "radio", "reset", "submit"}
)


@dataclass(frozen=True, slots=True)
class ActionProblem:
    """Why an element cannot receive an action right now."""

    reason: str
    waitable: bool
    """Whether the problem can clear on its own while the step waits."""


def action_problem(
    action: ActionType, identity: ElementIdentity, state: Actionability
) -> ActionProblem | None:
    """The first reason the action cannot be performed, or None when it can."""
    if not state.attached:
        return ActionProblem("detached", waitable=False)
    incompatible = _incompatibility(action, identity)
    if incompatible is not None:
        return ActionProblem(incompatible, waitable=False)
    if not state.visible:
        return ActionProblem("hidden", waitable=True)
    if action in (ActionType.CLICK, ActionType.SELECT) and not state.enabled:
        return ActionProblem("disabled", waitable=True)
    if action is ActionType.FILL and not state.editable:
        return ActionProblem("not_editable", waitable=True)
    return None


async def ensure_actionable(
    browser: BrowserPort,
    element: ElementRef,
    verified: ElementIdentity,
    action: ActionType,
    deadline: Deadline,
    scrubber: SecretScrubber,
) -> None:
    """Wait until the verified element can receive the action, or raise without acting.

    The identity is read again first: an element whose name changed after it was verified
    is not the element that was verified.
    """
    while True:
        epoch = await browser.dom_epoch(timeout_ms=deadline.timeout_ms())
        state = await browser.actionability(element)
        if not state.attached:
            raise TargetNotFound(
                "the target was removed from the page before the action",
                reason="detached_before_action",
            )
        current = await browser.identify(element, confirm=False)
        if not same_identity(current, verified):
            raise TargetDrifted(
                "the target changed after it was verified, so the action was not performed",
                reason="changed_before_action",
                verified=identity_report(verified, scrubber).model_dump(mode="json"),
                found=identity_report(current, scrubber).model_dump(mode="json"),
            )
        problem = action_problem(action, current, state)
        if problem is None:
            return
        if not problem.waitable or deadline.expired:
            raise TargetNotActionable(
                f"the target cannot receive the {action} action: "
                f"{problem.reason.replace('_', ' ')}",
                reason=problem.reason,
                action=action.value,
            )
        await browser.wait_for_dom_change(epoch, timeout_ms=deadline.timeout_ms())


def _incompatibility(action: ActionType, identity: ElementIdentity) -> str | None:
    tag = identity.tag.lower()
    if action is ActionType.SELECT and tag != "select":
        return "not_a_select"
    if action is ActionType.FILL:
        if tag == "select":
            return "select_needs_select_action"
        if tag == "input" and effective_type(tag, identity.input_type) in NON_FILLABLE_INPUT_TYPES:
            return "input_type_not_fillable"
    return None
