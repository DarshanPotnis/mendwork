"""Recording a person's interactions: which ones become steps, and which are ignored.

One scripted recording covers the whole interaction vocabulary; each test checks one rule.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from playwright.async_api import Browser

from mendwork.engine.domain.enums import ActionType, CheckpointKind
from mendwork.engine.domain.recording import (
    DraftStep,
    IgnoredReason,
    InteractionIgnored,
    LiteralDraft,
    NavigationIgnored,
)
from tests.integration.recording_harness import RecordingOutcome, ScriptedUser, record_scripted
from tests.integration.recording_pages import INTERACTIONS, ORIGIN, site

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]


async def person(user: ScriptedUser) -> None:
    page = user.page
    await page.click("#background")
    await page.click("#name")
    await page.keyboard.type("Adx")
    await page.keyboard.press("Backspace")
    await page.keyboard.type("a")
    await page.click("#when")
    await page.fill("#when", "2026-02-10")
    await user.steps(3)
    await page.click("#apply", modifiers=["Control"])
    await user.ignored(1)
    await page.click("#apply-label")
    await user.steps(4)
    await page.click("#next")
    await user.steps(5)
    await page.dblclick("#done")
    await user.steps(6)
    await user.ignored(2)
    await page.click("#upload")
    await user.ignored(3)
    await page.frame_locator("iframe").locator("#inner").click()
    await user.ignored(4)
    await page.goto(f"{ORIGIN}elsewhere.html")
    await user.steps(7)
    await page.click("#query")
    await page.keyboard.press("a")
    await user.navigation_noted()
    await page.click("#finish")
    await user.steps(8)


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def outcome(browser: Browser) -> AsyncIterator[RecordingOutcome]:
    yield await record_scripted(
        browser, f"{ORIGIN}profile.html", person, prepare=site(INTERACTIONS)
    )


def steps(outcome: RecordingOutcome) -> tuple[DraftStep, ...]:
    return outcome.recorded.steps


async def test_only_actions_that_change_something_become_steps(outcome: RecordingOutcome) -> None:
    assert [(step.action, step.description) for step in steps(outcome)] == [
        (ActionType.NAVIGATE, f"NAVIGATE to {ORIGIN}profile.html"),
        (ActionType.FILL, "FILL the 'Full name' field"),
        (ActionType.FILL, "FILL the 'Start date' field"),
        (ActionType.CLICK, "CLICK the 'Apply profile' button"),
        (ActionType.CLICK, "CLICK the 'Next page' link"),
        (ActionType.CLICK, "CLICK the 'Done' button"),
        (ActionType.NAVIGATE, f"NAVIGATE to {ORIGIN}elsewhere.html"),
        (ActionType.CLICK, "CLICK the 'Finish' button"),
    ]


async def test_keystrokes_in_one_field_become_one_fill_with_the_final_value(
    outcome: RecordingOutcome,
) -> None:
    name, when = steps(outcome)[1:3]

    assert name.value == LiteralDraft(value="Ada")
    assert when.value == LiteralDraft(value="2026-02-10")
    assert [checkpoint.kind for checkpoint in name.checkpoints] == [CheckpointKind.FIELD_HAS_VALUE]


async def test_a_click_on_a_child_element_records_its_actionable_ancestor(
    outcome: RecordingOutcome,
) -> None:
    apply = steps(outcome)[3]

    assert apply.target is not None
    assert (apply.target.tag, apply.target.accessible_name) == ("button", "Apply profile")
    assert [checkpoint.kind for checkpoint in apply.checkpoints] == [CheckpointKind.TEXT_PRESENT]
    assert apply.checkpoints[0].model_dump()["text"] == "Applied for Ada"


async def test_a_click_that_navigates_is_one_step(outcome: RecordingOutcome) -> None:
    link = steps(outcome)[4]

    assert [checkpoint.kind for checkpoint in link.checkpoints] == [
        CheckpointKind.URL_MATCHES,
        CheckpointKind.ELEMENT_VISIBLE,
    ]
    assert steps(outcome)[5].action is ActionType.CLICK


async def test_unrecordable_interactions_are_ignored_and_recording_continues(
    outcome: RecordingOutcome,
) -> None:
    ignored = [
        notice.reason for notice in outcome.notices if isinstance(notice, InteractionIgnored)
    ]

    assert ignored == [
        IgnoredReason.MODIFIED_CLICK,
        IgnoredReason.DOUBLE_CLICK,
        IgnoredReason.FILE_INPUT,
        IgnoredReason.FRAME,
    ]
    assert outcome.recorded.ignored_count == 4


async def test_a_page_the_person_opened_is_a_step_and_one_the_page_opened_is_not(
    outcome: RecordingOutcome,
) -> None:
    noted = [notice.url for notice in outcome.notices if isinstance(notice, NavigationIgnored)]

    assert steps(outcome)[6].value == LiteralDraft(value=f"{ORIGIN}elsewhere.html")
    assert noted == [f"{ORIGIN}final.html"]


async def test_the_recording_is_a_valid_workflow(outcome: RecordingOutcome) -> None:
    workflow = outcome.workflow("interactions")

    assert [step.id for step in workflow.steps] == [
        "open_profile",
        "fill_full_name",
        "fill_start_date",
        "click_apply_profile",
        "click_next_page",
        "click_done",
        "open_elsewhere",
        "click_finish",
    ]
