"""Every selector in every example finds exactly the element the chaos portal says it should.

The walk performs each step on the ground-truth element from ``window.__chaos.locate()``,
never through the selectors under test, so a wrong selector cannot mask itself by taking
the workflow somewhere else. Selectors are resolved with Rung 0's own primitive,
``resolve_unique``, and every target's computed identity must match its fingerprint and be
confirmed by Playwright's role locator.
"""

import asyncio

import pytest
from playwright.async_api import ElementHandle, expect

from benchmarks.chaos.workflow_targets import load_workflow_targets
from mendwork.adapters.browser_playwright.identity import identify
from mendwork.adapters.browser_playwright.locators import scope_locators
from mendwork.adapters.browser_playwright.resolution import resolve_unique
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.engine.domain.checkpoints import DownloadCompleted, ElementVisible
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.steps import ClickStep, FillStep, NavigateStep, Step, step_target
from mendwork.engine.domain.values import InputValue, LiteralValue, SecretValue, ValueRef
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.replay.identity import identity_differences
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
    resolution = await resolve_unique(portal.page, selector)
    assert resolution.element is not None, (
        f"{where}: {selector!r} matched {resolution.level_counts} visible elements per level"
    )
    try:
        assert await is_same_element(portal.page, resolution.element, truth), (
            f"{where}: {selector!r} found the wrong element"
        )
    finally:
        await resolution.element.dispose()


async def assert_identity_matches(
    portal: PortalDriver,
    scripts: PageScripts,
    target: Fingerprint,
    truth: ElementHandle,
    where: str,
) -> None:
    identity = await identify(portal.page, truth, scripts, confirm=True)
    assert identity_differences(target, identity) == (), f"{where}: {identity!r}"


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
async def test_every_selector_and_identity_matches_the_ground_truth_target(
    portal: PortalDriver, workflow_id: str
) -> None:
    workflow = load_example(workflow_id)
    targets = load_workflow_targets(workflow_id).targets
    scripts = await asyncio.to_thread(PageScripts.load)
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
            where = f"{workflow_id}.{step.id}"
            for rank, selector in enumerate(target.selectors):
                await assert_selector_finds(portal, selector, truth, f"{where} selectors[{rank}]")
                checked += 1
            await assert_identity_matches(portal, scripts, target, truth, where)

        await perform(portal, step, truth, next_targeted_page(workflow, index, targets), current)

        for checkpoint in step.checkpoints:
            if isinstance(checkpoint, ElementVisible):
                for level in scope_locators(portal.page, checkpoint.selector):
                    await expect(level).to_have_count(1)
                    await expect(level).to_be_visible()

    assert checked == sum(
        len(t.selectors) for step in workflow.steps if (t := step_target(step)) is not None
    )
