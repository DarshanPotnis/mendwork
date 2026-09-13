"""Recording targets and checkpoints in a real browser: verification, scoping, and failures."""

import pytest
from playwright.async_api import Browser

from mendwork.engine.domain.enums import CheckpointKind, SelectorStrategy
from mendwork.engine.domain.recording import DropReason
from mendwork.engine.recording.selectors import summarize
from tests.integration.recording_harness import ScriptedUser, record_scripted
from tests.integration.recording_pages import ORIGIN, TARGETS, site
from tests.integration.replay_harness import replay_settings

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]


async def test_a_selector_that_matches_two_elements_is_dropped_and_another_kept(
    browser: Browser,
) -> None:
    async def person(user: ScriptedUser) -> None:
        await user.page.get_by_test_id("save-primary").click()
        await user.steps(2)

    outcome = await record_scripted(
        browser, f"{ORIGIN}duplicates.html", person, prepare=site(TARGETS)
    )

    click = outcome.recorded.steps[1]
    assert click.target is not None
    assert [selector.strategy for selector in click.target.selectors] == [SelectorStrategy.TEST_ID]
    assert [(item.summary, item.reason, item.level_counts) for item in click.dropped_selectors] == [
        ("role_name button 'Keep'", DropReason.AMBIGUOUS, (2,)),
        ("text 'Keep'", DropReason.AMBIGUOUS, (2,)),
    ]


async def test_no_surviving_selector_ends_the_recording_with_nothing_to_write(
    browser: Browser,
) -> None:
    async def person(user: ScriptedUser) -> None:
        await user.page.get_by_role("button", name="Go").first.click()
        await user.steps(2)

    outcome = await record_scripted(browser, f"{ORIGIN}twins.html", person, prepare=site(TARGETS))

    assert outcome.recording is None
    assert outcome.error is not None
    assert outcome.error.context["reason"] == "no_selector"
    assert "no selector finds exactly this button" in outcome.error.message


async def test_a_repeated_control_is_scoped_to_its_row(browser: Browser) -> None:
    async def person(user: ScriptedUser) -> None:
        await user.page.get_by_role("row", name="PO-2").get_by_role("button").click()
        await user.steps(2)

    outcome = await record_scripted(browser, f"{ORIGIN}rows.html", person, prepare=site(TARGETS))

    view = outcome.recorded.steps[1]
    assert view.target is not None
    assert [summarize(selector) for selector in view.target.selectors] == [
        "role_name button 'View' within role_name row 'PO-2' (substring)",
        "text 'View' within role_name row 'PO-2' (substring)",
    ]
    assert view.target.nearby_text == ("Orders", "PO-2", "Actions")
    assert [checkpoint.kind for checkpoint in view.checkpoints] == [CheckpointKind.ELEMENT_VISIBLE]


async def test_a_page_that_never_stops_changing_ends_the_recording(browser: Browser) -> None:
    async def person(user: ScriptedUser) -> None:
        await user.page.click("#save")
        await user.steps(2)

    outcome = await record_scripted(
        browser,
        f"{ORIGIN}restless.html",
        person,
        prepare=site(TARGETS),
        settings=replay_settings(step_timeout_ms=800, settle_timeout_ms=100),
    )

    assert outcome.error is not None
    assert outcome.error.context["reason"] == "page_never_stable"
    assert outcome.error.message == (
        "the page kept changing while this step was recorded, so its selectors could not be "
        "verified"
    )


async def test_a_new_window_ends_the_recording(browser: Browser) -> None:
    async def person(user: ScriptedUser) -> None:
        await user.page.get_by_role("button", name="Open window").click()
        await user.steps(2)

    outcome = await record_scripted(browser, f"{ORIGIN}popup.html", person, prepare=site(TARGETS))

    assert outcome.error is not None
    assert outcome.error.context["reason"] == "new_page_opened"


async def test_each_proposed_checkpoint_is_verified_and_a_failing_one_dropped(
    browser: Browser,
) -> None:
    async def person(user: ScriptedUser) -> None:
        await user.page.get_by_role("link", name="Export report").click()
        await user.steps(2)
        await user.page.get_by_role("button", name="Add results").click()
        await user.steps(3)
        await user.page.get_by_role("button", name="Apply filter").click()
        await user.steps(4)

    outcome = await record_scripted(
        browser,
        f"{ORIGIN}checkpoints.html",
        person,
        prepare=site(TARGETS),
        settings=replay_settings(record_checkpoint_timeout_ms=300),
    )

    export, add, apply = outcome.recorded.steps[1:]
    assert export.checkpoints[0].model_dump()["filename_pattern"] == r"report\.csv"
    assert add.checkpoints == ()
    assert [(item.kind, item.reason) for item in add.dropped_checkpoints] == [
        (CheckpointKind.ELEMENT_VISIBLE, "did not pass (timeout)")
    ]
    assert [checkpoint.kind for checkpoint in apply.checkpoints] == [
        CheckpointKind.TEXT_PRESENT,
        CheckpointKind.NO_ERROR_BANNER,
    ]
