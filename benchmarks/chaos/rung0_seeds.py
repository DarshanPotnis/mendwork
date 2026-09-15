"""Survey chaos seeds: where download_report stops safely, succeeds, or heals, against ground truth.

Each seed replays the complete example workflow with the same wiring as ``mendwork run``,
against a running portal (``make portal``), at the given level. Every action is checked against
``window.__chaos.locate`` at the moment it happens (the same wrapper the heal fixture suite
uses), and the page's recorded wrong actions are read before the browser context closes. A
separate browser context reads ``window.__chaos.applied`` on each page the workflow visits:
benchmark code may read the ground truth, Mendwork never does.

- A stop is attributed to the mutations applied to the failing step's own target.
- A **wrong action** is an action on an element that is not the step's target, any action on a
  target that received an abstain_expected mutation, or a wrong action the page recorded.
- A **false success** is a wrong action whose step still passed its checkpoints: the worst
  outcome, because nothing in the run's record would show it.

    uv run python -m benchmarks.chaos.rung0_seeds \
        --portal http://127.0.0.1:8765/ --level 3 --seeds 0-30
"""

import argparse
import asyncio
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from playwright.async_api import Browser, Page, async_playwright
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from benchmarks.chaos.ground_truth import ActionCheck
from benchmarks.chaos.heal_cases import run_with_ground_truth
from benchmarks.chaos.workflow_targets import load_workflow_targets
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.runs import Run, RunStatus, StepStatus
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.observability import configure_logging
from mendwork.settings import Settings

WORKFLOW_ID: Final = "download_report"
WORKFLOW_PATH: Final = (
    Path(__file__).resolve().parents[2] / "workflows" / "examples" / "download_report.yaml"
)
# The pages download_report visits, in order, and their files.
PAGES: Final = (
    ("login", "index.html"),
    ("dashboard", "dashboard.html"),
    ("reports", "reports.html"),
)
READY_TIMEOUT_MS: Final = 5_000
ABSTAIN_EXPECTED: Final = "abstain_expected"
# The portal's fictional demo account, documented in chaos-portal/README.md.
DEMO_EMAIL: Final = "buyer@harborline.test"
DEMO_PASSWORD: Final = "harbor-demo"  # noqa: S105 - a fictional, documented demo credential


class Applied(BaseModel):
    """One entry of ``window.__chaos.applied``."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str
    category: str
    target_key: str | None = Field(alias="targetKey")
    description: str


_APPLIED: Final[TypeAdapter[list[Applied]]] = TypeAdapter(list[Applied])


@dataclass(frozen=True)
class SeedOutcome:
    """One seed's run, every action checked against ground truth, and what caused a stop."""

    seed: int
    run: Run
    applied: Mapping[str, tuple[Applied, ...]]
    cause: tuple[Applied, ...]
    checks: tuple[ActionCheck, ...] = ()
    page_wrong_actions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WrongAction:
    """An action that should not have happened, and whether its step still succeeded."""

    step_id: str | None
    detail: str
    step_succeeded: bool


def parse_seeds(text: str) -> range:
    """``7`` or an inclusive range such as ``0-30``."""
    first, separator, last = text.partition("-")
    try:
        start = int(first)
        stop = int(last) if separator else start
    except ValueError:
        raise argparse.ArgumentTypeError("seeds look like 7 or 0-30") from None
    if start < 0 or stop < start:
        raise argparse.ArgumentTypeError("seeds must be a non-negative, ascending range")
    return range(start, stop + 1)


def attribute(
    run: Run, targets: Mapping[str, str], applied: Mapping[str, Sequence[Applied]]
) -> tuple[Applied, ...]:
    """The mutations applied to the failing step's own target."""
    failed = run.failed_step
    if failed is None:
        return ()
    key = targets.get(failed.step_id)
    if key is None:
        return ()
    page = key.split(".", 1)[0]
    return tuple(mutation for mutation in applied.get(page, ()) if mutation.target_key == key)


def checked_actions(outcome: SeedOutcome) -> tuple[ActionCheck, ...]:
    """The actions ground truth could check: every action on a step with a target key."""
    return tuple(check for check in outcome.checks if check.target_key is not None)


def wrong_actions(outcome: SeedOutcome) -> tuple[WrongAction, ...]:
    """Every action that reached the wrong element or an abstain target, and every wrong action
    the page recorded."""
    abstain_targets = {
        mutation.target_key
        for mutations in outcome.applied.values()
        for mutation in mutations
        if mutation.category == ABSTAIN_EXPECTED and mutation.target_key is not None
    }
    succeeded = {step.step_id for step in outcome.run.steps if step.status is StepStatus.SUCCEEDED}
    found: list[WrongAction] = []
    for check in checked_actions(outcome):
        if check.correct is False:
            detail = f"{check.action} at {check.step_id} did not reach {check.target_key}"
        elif check.target_key in abstain_targets:
            detail = f"{check.action} at {check.step_id} on {check.target_key}, which must abstain"
        else:
            continue
        found.append(WrongAction(check.step_id, detail, check.step_id in succeeded))
    found.extend(
        WrongAction(None, f"the page recorded a wrong action: {label}", False)
        for label in outcome.page_wrong_actions
    )
    return tuple(found)


def false_successes(outcome: SeedOutcome) -> tuple[WrongAction, ...]:
    """Wrong actions whose step passed its checkpoints anyway."""
    return tuple(wrong for wrong in wrong_actions(outcome) if wrong.step_succeeded)


def describe(outcome: SeedOutcome) -> str:
    """One line per seed: the outcome, ground truth, its cause, and every page's mutations."""
    pages = " | ".join(
        f"{page}: " + (", ".join(f"{m.id}({m.target_key or 'page'})" for m in mutations) or "none")
        for page, mutations in outcome.applied.items()
    )
    run = outcome.run
    healed = "; ".join(
        f"{step.step_id} at rung {step.heal.healed_rung}"
        for step in run.steps
        if step.heal is not None and step.heal.healed_rung is not None
    )
    heals = f" | healed: {healed}" if healed else ""
    wrong = wrong_actions(outcome)
    truth = f" | checked {len(checked_actions(outcome))} actions, {len(wrong)} wrong"
    if false_successes(outcome):
        truth += ", FALSE SUCCESS: " + "; ".join(item.detail for item in false_successes(outcome))
    elif wrong:
        truth += ": " + "; ".join(item.detail for item in wrong)
    if run.status is RunStatus.SUCCEEDED:
        return f"seed {outcome.seed}: succeeded{heals}{truth} | {pages}"
    failed = run.failed_step
    where = f"step {failed.index + 1} {failed.step_id}" if failed is not None else "before any step"
    error = run.error.type if run.error is not None else "error"
    reason = run.error.context.get("reason") if run.error is not None else None
    if error == "HealAbstained" and reason is not None:
        error = f"{error} ({reason})"
    cause = (
        "; ".join(f"{m.id} on {m.target_key}: {m.description}" for m in outcome.cause)
        or "no mutation targets this step directly"
    )
    return (
        f"seed {outcome.seed}: stopped at {where} with {error}{heals}{truth} | cause: {cause} "
        f"| {pages}"
    )


async def ground_truth(
    browser: Browser, portal: str, seed: int, level: int
) -> dict[str, tuple[Applied, ...]]:
    """The mutations each visited page receives for a seed and level."""
    context = await browser.new_context()
    try:
        page = await context.new_page()
        applied = {"login": await _applied(page, f"{portal}index.html?seed={seed}&level={level}")}
        await _sign_in(page, portal)
        for page_id, path in PAGES[1:]:
            applied[page_id] = await _applied(page, f"{portal}{path}?seed={seed}&level={level}")
        return applied
    finally:
        await context.close()


async def _applied(page: Page, url: str) -> tuple[Applied, ...]:
    await page.goto(url)
    await page.wait_for_function("() => window.__chaos?.ready === true", timeout=READY_TIMEOUT_MS)
    return tuple(_APPLIED.validate_python(await page.evaluate("() => window.__chaos.applied")))


async def _sign_in(page: Page, portal: str) -> None:
    await _applied(page, f"{portal}index.html?level=0")
    await page.get_by_label("Email address").fill(DEMO_EMAIL)
    await page.get_by_label("Password").fill(DEMO_PASSWORD)
    await page.get_by_role("button", name="Sign in").click()
    await page.wait_for_url("**/dashboard.html", timeout=READY_TIMEOUT_MS)


async def survey(portal: str, level: int, seeds: range, out: TextIO) -> list[SeedOutcome]:
    """Replay every seed with ground truth and report each one as it finishes."""
    settings = Settings()
    configure_logging(settings, SecretScrubber())
    content = await asyncio.to_thread(WORKFLOW_PATH.read_bytes)
    workflow: WorkflowVersion = WorkflowYamlCodec(max_bytes=settings.workflow_max_bytes).decode(
        content, source=str(WORKFLOW_PATH)
    )
    targets = load_workflow_targets(WORKFLOW_ID).targets
    outcomes: list[SeedOutcome] = []
    with tempfile.TemporaryDirectory(prefix="mendwork-seed-survey-") as scratch:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                for seed in seeds:
                    inputs = {
                        "portal_url": f"{portal}index.html?seed={seed}&level={level}",
                        "account_email": DEMO_EMAIL,
                    }
                    replay = await run_with_ground_truth(
                        browser,
                        workflow,
                        inputs,
                        targets,
                        Path(scratch) / str(seed),
                        settings=settings,
                        signed_in=False,
                    )
                    applied = await ground_truth(browser, portal, seed, level)
                    outcome = SeedOutcome(
                        seed,
                        replay.run,
                        applied,
                        attribute(replay.run, targets, applied),
                        replay.checks,
                        replay.wrong_actions,
                    )
                    outcomes.append(outcome)
                    out.write(describe(outcome) + "\n")
                    out.flush()
            finally:
                await browser.close()
    out.write(summary(outcomes, portal, level) + "\n")
    return outcomes


def summary(outcomes: Sequence[SeedOutcome], portal: str, level: int) -> str:
    """Ground truth totals, then the first stop and the first success with their commands."""
    checked = sum(len(checked_actions(outcome)) for outcome in outcomes)
    wrong = sum(len(wrong_actions(outcome)) for outcome in outcomes)
    false = sum(len(false_successes(outcome)) for outcome in outcomes)
    lines = [
        f"ground truth: {checked} actions checked, {wrong} wrong, {false} false successes "
        f"across {len(outcomes)} seeds"
    ]
    for label, wanted in (
        ("first stop", RunStatus.FAILED),
        ("first success", RunStatus.SUCCEEDED),
    ):
        found = next((outcome for outcome in outcomes if outcome.run.status is wanted), None)
        if found is None:
            lines.append(f"{label}: none in this range")
            continue
        lines.append(
            f"{label}: seed {found.seed} — mendwork run workflows/examples/download_report.yaml "
            f"--input 'portal_url={portal}index.html?seed={found.seed}&level={level}' "
            f"--input account_email={DEMO_EMAIL}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.chaos.rung0_seeds", description=__doc__
    )
    parser.add_argument(
        "--portal", default="http://127.0.0.1:8765/", help="the running portal's base URL"
    )
    parser.add_argument("--level", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--seeds", type=parse_seeds, default=parse_seeds("0-30"))
    arguments = parser.parse_args(argv)
    portal = arguments.portal if arguments.portal.endswith("/") else f"{arguments.portal}/"
    outcomes = asyncio.run(survey(portal, arguments.level, arguments.seeds, sys.stdout))
    return 1 if any(wrong_actions(outcome) for outcome in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
