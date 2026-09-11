"""Every selector in every example finds exactly the element the chaos portal says it should.

The walk performs each step on the ground-truth element from ``window.__chaos.locate()``,
never through the selectors under test, so a wrong selector cannot mask itself by taking
the workflow somewhere else. Checkpoint evaluation is the verifier's job (Phase 3); here
only element_visible selectors are resolved.
"""

import pytest
from playwright.async_api import ElementHandle, expect

from benchmarks.chaos.workflow_targets import load_workflow_targets
from mendwork.adapters.browser_playwright.locators import scope_locators
from mendwork.engine.domain.checkpoints import DownloadCompleted, ElementVisible
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.steps import ClickStep, FillStep, NavigateStep, Step, step_target
from mendwork.engine.domain.values import InputValue, LiteralValue, SecretValue, ValueRef
from mendwork.engine.domain.workflow import WorkflowVersion
from tests.integration.portal import (
    DEMO_EMAIL,
    DEMO_PASSWORD,
    PAGE_IDS,
    PageId,
    PortalDriver,
    is_same_element,
)
from tests.workflows import EXAMPLE_IDS, load_example

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]


def resolve(value: ValueRef, portal: PortalDriver) -> str:
    match value:
        case LiteralValue():
            return value.value
        case InputValue():
            return {"portal_url": portal.url("login"), "account_email": DEMO_EMAIL}[value.name]
        case SecretValue():
            return {"portal_password": DEMO_PASSWORD}[value.name]


def page_of(target_key: str) -> PageId:
    prefix = target_key.split(".")[0]
    for page_id in PAGE_IDS:
        if page_id == prefix:
            return page_id
    raise AssertionError(f"unknown page in target key {target_key}")


async def assert_selector_finds(
    portal: PortalDriver, selector: Selector, truth: ElementHandle, where: str
) -> None:
    levels = scope_locators(portal.page, selector)
    for depth, level in enumerate(levels):
        count = await level.count()
        assert count == 1, f"{where}: scope level {depth} of {selector!r} matched {count} elements"
    element = await levels[-1].element_handle()
    assert element is not None
    assert await element.is_visible(), f"{where}: {selector!r} found a hidden element"
    assert await is_same_element(portal.page, element, truth), (
        f"{where}: {selector!r} found the wrong element"
    )


async def perform(
    portal: PortalDriver,
    step: Step,
    truth: ElementHandle | None,
    next_page: PageId | None,
    current: PageId | None,
) -> None:
    match step:
        case NavigateStep():
            await portal.page.goto(resolve(step.value, portal))
            await portal.wait_ready()
        case FillStep() if truth is not None:
            await truth.fill(resolve(step.value, portal))
        case ClickStep() if truth is not None:
            if any(isinstance(checkpoint, DownloadCompleted) for checkpoint in step.checkpoints):
                async with portal.page.expect_download() as download:
                    await truth.click()
                await download.value
            elif next_page is not None and next_page != current:
                async with portal.expect_page(next_page):
                    await truth.click()
            else:
                await truth.click()
        case _:
            raise AssertionError(f"the examples do not use {step.action} steps yet")


def next_targeted_page(
    workflow: WorkflowVersion, index: int, targets: dict[str, str]
) -> PageId | None:
    for later in workflow.steps[index + 1 :]:
        if later.id in targets:
            return page_of(targets[later.id])
    return None


@pytest.mark.parametrize("workflow_id", EXAMPLE_IDS)
async def test_every_selector_resolves_to_the_ground_truth_target(
    portal: PortalDriver, workflow_id: str
) -> None:
    workflow = load_example(workflow_id)
    targets = load_workflow_targets(workflow_id).targets
    checked = 0

    for index, step in enumerate(workflow.steps):
        target = step_target(step)
        truth: ElementHandle | None = None
        current: PageId | None = None
        if target is not None:
            state = await portal.wait_ready()
            assert (state.level, state.applied) == (0, ())
            key = targets[step.id]
            current = page_of(key)
            assert state.page_id == current, f"{step.id} expects the {current} page"
            truth = await portal.locate(key)
            for rank, selector in enumerate(target.selectors):
                await assert_selector_finds(
                    portal, selector, truth, f"{workflow_id}.{step.id} selectors[{rank}]"
                )
                checked += 1

        await perform(portal, step, truth, next_targeted_page(workflow, index, targets), current)

        for checkpoint in step.checkpoints:
            if isinstance(checkpoint, ElementVisible):
                for level in scope_locators(portal.page, checkpoint.selector):
                    await expect(level).to_have_count(1)
                    await expect(level).to_be_visible()

    assert checked == sum(
        len(t.selectors) for step in workflow.steps if (t := step_target(step)) is not None
    )
