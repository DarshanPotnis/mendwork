"""Pre-action checks: what each action needs, what is worth waiting for, and what is not."""

import pytest

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.errors import TargetDrifted, TargetNotActionable, TargetNotFound
from mendwork.engine.ports.browser_types import Actionability, ElementIdentity
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.engine.verification.preaction import ActionProblem, action_problem, ensure_actionable
from tests.fakes.browser import FakeBrowser
from tests.unit.replay.builders import TEST_ID, browser, button

READY = Actionability(attached=True, visible=True, enabled=True, editable=True)


def identity(tag: str = "button", input_type: str | None = None) -> ElementIdentity:
    return ElementIdentity(tag=tag, input_type=input_type, role="button", name="Go")


def state(**fields: bool) -> Actionability:
    return READY.model_copy(update=fields)


@pytest.mark.parametrize(
    ("action", "element", "situation", "problem"),
    [
        (ActionType.CLICK, identity(), READY, None),
        (
            ActionType.CLICK,
            identity(),
            state(attached=False),
            ActionProblem("detached", waitable=False),
        ),
        (
            ActionType.CLICK,
            identity(),
            state(visible=False),
            ActionProblem("hidden", waitable=True),
        ),
        (
            ActionType.CLICK,
            identity(),
            state(enabled=False),
            ActionProblem("disabled", waitable=True),
        ),
        (
            ActionType.FILL,
            identity("input", "email"),
            state(editable=False),
            ActionProblem("not_editable", waitable=True),
        ),
        (
            ActionType.FILL,
            identity("input", "checkbox"),
            READY,
            ActionProblem("input_type_not_fillable", waitable=False),
        ),
        (ActionType.FILL, identity("input", "date"), READY, None),
        (
            ActionType.FILL,
            identity("select"),
            READY,
            ActionProblem("select_needs_select_action", waitable=False),
        ),
        (
            ActionType.SELECT,
            identity("button"),
            READY,
            ActionProblem("not_a_select", waitable=False),
        ),
        (
            ActionType.SELECT,
            identity("select"),
            state(enabled=False),
            ActionProblem("disabled", waitable=True),
        ),
        (ActionType.PRESS, identity(), state(enabled=False), None),
    ],
)
def test_each_action_needs_what_it_needs(
    action: ActionType,
    element: ElementIdentity,
    situation: Actionability,
    problem: ActionProblem | None,
) -> None:
    assert action_problem(action, element, situation) == problem


async def check(page: FakeBrowser, action: ActionType = ActionType.CLICK) -> None:
    match = await page.resolve_unique(TEST_ID)
    assert match.element is not None
    expected = ElementIdentity(tag="button", role="button", name="Download CSV")
    await ensure_actionable(
        page, match.element, expected, action, Deadline.after(page.timer, 1_000), SecretScrubber()
    )


@pytest.mark.asyncio
async def test_a_ready_element_passes_without_waiting() -> None:
    page = browser(elements={"go": button()}, finds={TEST_ID: "go"})

    await check(page)

    assert page.calls_named("wait_for_dom_change") == []


@pytest.mark.asyncio
async def test_a_disabled_button_is_waited_for_until_the_page_enables_it() -> None:
    page = browser(elements={"go": button(enabled=False)}, finds={TEST_ID: "go"})
    page.changes.append(lambda b: setattr(b.elements["go"], "enabled", True))

    await check(page)

    assert page.calls_named("wait_for_dom_change") == ["wait_for_dom_change"]


@pytest.mark.asyncio
async def test_a_button_that_never_enables_fails_at_the_deadline() -> None:
    page = browser(elements={"go": button(enabled=False)}, finds={TEST_ID: "go"})

    with pytest.raises(TargetNotActionable) as caught:
        await check(page)

    assert caught.value.context["reason"] == "disabled"
    assert page.timer.monotonic() >= 1001.0


@pytest.mark.asyncio
async def test_an_incompatible_element_fails_at_once() -> None:
    page = browser(elements={"go": button()}, finds={TEST_ID: "go"})

    with pytest.raises(TargetNotActionable) as caught:
        await check(page, ActionType.SELECT)

    assert caught.value.context["reason"] == "not_a_select"
    assert page.calls_named("wait_for_dom_change") == []


@pytest.mark.asyncio
async def test_a_detached_element_is_not_found() -> None:
    page = browser(elements={"go": button(attached=False)}, finds={TEST_ID: "go"})

    with pytest.raises(TargetNotFound) as caught:
        await check(page)

    assert caught.value.context["reason"] == "detached_before_action"


@pytest.mark.asyncio
async def test_an_element_renamed_after_verification_is_never_acted_on() -> None:
    page = browser(elements={"go": button("Delete data")}, finds={TEST_ID: "go"})

    with pytest.raises(TargetDrifted) as caught:
        await check(page)

    assert caught.value.context["reason"] == "changed_before_action"
    assert caught.value.context["found"]["name"] == "Delete data"  # type: ignore[index]
