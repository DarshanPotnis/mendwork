"""Find chaos seeds where download_report stops safely at Rung 0, and seeds where it succeeds.

Each seed replays the example workflow with the same wiring as ``mendwork run``, against a
running portal (``make portal``), at the given level. A separate browser context then reads
``window.__chaos.applied`` on each page the workflow visits, for that seed and level:
benchmark code may read the ground truth, Mendwork never does. A stop is attributed to the
mutations applied to the failing step's own target.

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

from benchmarks.chaos.workflow_targets import load_workflow_targets
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.secrets_env.naming import secret_variable_name
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.wiring import build_replayer, session_options
from mendwork.engine.domain.events import RunEvent
from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.domain.runs import Run, RunStatus
from mendwork.engine.domain.workflow import WorkflowVersion
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
# The portal's fictional demo account, documented in chaos-portal/README.md.
DEMO_EMAIL: Final = "buyer@harborline.test"
DEMO_PASSWORD: Final = "harbor-demo"  # noqa: S105 - a fictional, documented demo credential


class Applied(BaseModel):
    """One entry of ``window.__chaos.applied``."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str
    target_key: str | None = Field(alias="targetKey")
    description: str


_APPLIED: Final[TypeAdapter[list[Applied]]] = TypeAdapter(list[Applied])


@dataclass(frozen=True)
class SeedOutcome:
    """One seed's run, the mutations each page received, and what caused a stop."""

    seed: int
    run: Run
    applied: Mapping[str, tuple[Applied, ...]]
    cause: tuple[Applied, ...]


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


def describe(outcome: SeedOutcome) -> str:
    """One line per seed: the outcome, its cause, and every page's mutations."""
    pages = " | ".join(
        f"{page}: " + (", ".join(f"{m.id}({m.target_key or 'page'})" for m in mutations) or "none")
        for page, mutations in outcome.applied.items()
    )
    run = outcome.run
    if run.status is RunStatus.SUCCEEDED:
        return f"seed {outcome.seed}: succeeded | {pages}"
    failed = run.failed_step
    where = f"step {failed.index + 1} {failed.step_id}" if failed is not None else "before any step"
    error = run.error.type if run.error is not None else "error"
    cause = (
        "; ".join(f"{m.id} on {m.target_key}: {m.description}" for m in outcome.cause)
        or "no mutation targets this step directly"
    )
    return f"seed {outcome.seed}: stopped at {where} with {error} | cause: {cause} | {pages}"


class _Discard:
    """Progress is not needed here; each run's record carries the outcome."""

    async def emit(self, event: RunEvent) -> None:
        return None


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
    """Replay every seed and report each one as it finishes."""
    settings = Settings()
    configure_logging(settings)
    content = await asyncio.to_thread(WORKFLOW_PATH.read_bytes)
    workflow: WorkflowVersion = WorkflowYamlCodec(max_bytes=settings.workflow_max_bytes).decode(
        content, source=str(WORKFLOW_PATH)
    )
    targets = load_workflow_targets(WORKFLOW_ID).targets
    environ = {secret_variable_name(SecretName("portal_password")): DEMO_PASSWORD}
    outcomes: list[SeedOutcome] = []
    with tempfile.TemporaryDirectory(prefix="mendwork-rung0-seeds-") as scratch:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                launcher = await PlaywrightLauncher.create(browser, session_options(settings))
                replayer = build_replayer(
                    settings,
                    launcher=launcher,
                    artifacts=LocalArtifactStore(Path(scratch)),
                    events=_Discard(),
                    environ=environ,
                )
                for seed in seeds:
                    inputs = {
                        "portal_url": f"{portal}index.html?seed={seed}&level={level}",
                        "account_email": DEMO_EMAIL,
                    }
                    run = await replayer.run(workflow, inputs)
                    applied = await ground_truth(browser, portal, seed, level)
                    outcome = SeedOutcome(seed, run, applied, attribute(run, targets, applied))
                    outcomes.append(outcome)
                    out.write(describe(outcome) + "\n")
                    out.flush()
            finally:
                await browser.close()
    out.write(summary(outcomes, portal, level) + "\n")
    return outcomes


def summary(outcomes: Sequence[SeedOutcome], portal: str, level: int) -> str:
    """The first stop and the first success, with the command that reproduces each."""
    lines = []
    for label, wanted in (
        ("first Rung 0 stop", RunStatus.FAILED),
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
    asyncio.run(survey(portal, arguments.level, arguments.seeds, sys.stdout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
