"""The labels walk: doing the task on a release with the labels alone (ADR 0014).

Before Mendwork is allowed near a release, the labels must prove themselves: each one matches
exactly one visible control, and following them in order performs the whole task. The walk saves a
screenshot per step, which is what a person approves the labels against. It uses no Mendwork
machinery: plain Playwright, so a label is never right merely because the product agrees with it.
"""

import asyncio
import hashlib
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from playwright.async_api import Browser, Page
from playwright.async_api import Error as PlaywrightError

from benchmarks.real_apps.labels import Expect, PairLabels
from mendwork.engine.domain.steps import ClickStep, FillStep, NavigateStep, PressStep, SelectStep
from mendwork.engine.domain.values import InputValue, LiteralValue, SecretValue
from mendwork.engine.domain.workflow import WorkflowVersion

STEP_TIMEOUT_MS: Final = 15_000
SETTLE_TIMEOUT_MS: Final = 10_000
NAVIGATION_MS: Final = 2_000


class WalkFailedError(RuntimeError):
    """The labels did not perform the task, so they are not ground truth yet."""


@dataclass(frozen=True, slots=True)
class WalkResult:
    """What the walk did: the steps it performed, and the screenshot of each."""

    walked: tuple[str, ...]
    screenshots: dict[str, str]
    """Step id to the SHA-256 of the image a person approves it against."""


async def walk(
    browser: Browser,
    workflow: WorkflowVersion,
    labels: PairLabels,
    *,
    base_url: str,
    inputs: Mapping[str, str],
    secrets: Mapping[str, str],
    directory: Path,
) -> WalkResult:
    """Perform the workflow on the release using only the labels, one screenshot per step."""
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    by_step = {step.step_id: step for step in labels.steps}
    walked: list[str] = []
    shots: dict[str, str] = {}
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = await context.new_page()
        for step in workflow.steps:
            label = by_step.get(step.id)
            if isinstance(step, NavigateStep):
                await page.goto(_value(step.value, inputs, secrets, base_url), wait_until="load")
            elif label is None or label.expect is Expect.ABSTAIN:
                continue
            else:
                await _act(page, step, label.selector or "", inputs, secrets, base_url)
            # Screenshot what the step led to, not the moment it was sent: a submit that navigates
            # would otherwise be approved against a spinner. Wait briefly for a navigation to start,
            # then for the document it lands on; an action that navigates nowhere just goes on.
            with suppress(PlaywrightError):
                await page.wait_for_event("framenavigated", timeout=NAVIGATION_MS)
            with suppress(PlaywrightError):
                await page.wait_for_load_state("load", timeout=SETTLE_TIMEOUT_MS)
            image = directory / f"{len(walked):02d}-{step.id}.png"
            await page.screenshot(path=image)
            data = await asyncio.to_thread(image.read_bytes)
            shots[step.id] = "sha256:" + hashlib.sha256(data).hexdigest()
            walked.append(step.id)
    finally:
        await context.close()
    missing = sorted(set(by_step) - set(walked))
    if missing:
        raise WalkFailedError(f"the walk never performed {missing}")
    return WalkResult(tuple(walked), shots)


async def _act(
    page: Page,
    step: ClickStep | FillStep | SelectStep | PressStep | NavigateStep,
    selector: str,
    inputs: Mapping[str, str],
    secrets: Mapping[str, str],
    base_url: str,
) -> None:
    locator = page.locator(selector)
    found = await locator.count()
    if found != 1:
        raise WalkFailedError(f"{step.id}: the label matches {found} elements, not one: {selector}")
    match step:
        case ClickStep():
            await locator.click(timeout=STEP_TIMEOUT_MS)
        case FillStep():
            await locator.fill(
                _value(step.value, inputs, secrets, base_url), timeout=STEP_TIMEOUT_MS
            )
        case SelectStep():
            await locator.select_option(
                label=_value(step.value, inputs, secrets, base_url), timeout=STEP_TIMEOUT_MS
            )
        case PressStep():
            await locator.press(step.key, timeout=STEP_TIMEOUT_MS)
        case NavigateStep():
            raise WalkFailedError(f"{step.id}: a navigate step has no control to act on")


def _value(
    value: LiteralValue | InputValue | SecretValue,
    inputs: Mapping[str, str],
    secrets: Mapping[str, str],
    base_url: str,
) -> str:
    match value:
        case LiteralValue():
            return value.value
        case InputValue():
            return inputs[value.name]
        case SecretValue():
            return secrets[value.name]
