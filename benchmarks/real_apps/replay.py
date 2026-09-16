"""Replaying a workflow on a release, with a person's labels as ground truth (ADR 0014).

The wiring is ``mendwork run``'s own, as the chaos benchmark uses it; only the ground-truth probes
differ, because a real application cannot say which control a step meant. Nothing about the product
changes: it sees a URL, and the benchmark watches what it acts on.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from playwright.async_api import Browser

from benchmarks.chaos.ground_truth import ActionCheck, GroundTruthLauncher, StepTracker
from benchmarks.chaos.local_egress import local_policy
from benchmarks.chaos.models import ModelMode, model_rung_for
from benchmarks.real_apps.truth import LabelProbes
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.secrets_env.naming import secret_variable_name
from mendwork.adapters.system.resolver import SystemHostResolver
from mendwork.apps.cli.wiring import build_replayer, egress_enforcement, session_options
from mendwork.engine.domain.events import RunEvent
from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.domain.runs import Run
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings


@dataclass(frozen=True, slots=True)
class LabelledRun:
    """A replay whose every action was checked against the labels."""

    run: Run
    events: tuple[RunEvent, ...]
    checks: tuple[ActionCheck, ...]
    tracker: StepTracker


async def run_with_labels(
    browser: Browser,
    workflow: WorkflowVersion,
    inputs: Mapping[str, str],
    secrets: Mapping[str, str],
    targets: Mapping[str, str],
    probes: LabelProbes,
    directory: Path,
    *,
    settings: Settings,
    model: ModelMode,
    abstain_steps: frozenset[str],
    client: httpx.AsyncClient | None,
) -> LabelledRun:
    """Replay the workflow as ``mendwork run`` would, checking every action against the labels."""
    tracker = StepTracker()
    checks: list[ActionCheck] = []
    wrong_actions: list[str] = []
    resolver = SystemHostResolver()
    inner = await PlaywrightLauncher.create(
        browser, session_options(settings), egress_enforcement(settings, resolver)
    )
    launcher = GroundTruthLauncher(
        inner,
        targets,
        tracker,
        checks,
        wrong_actions,
        signed_in=False,
        matched=probes.matched,
        available=probes.available,
        wrong_count=probes.wrong_count,
        target_index=probes.target_index,
        known=probes.known,
    )
    artifacts = LocalArtifactStore(directory)
    rung = model_rung_for(
        model,
        settings,
        tracker=tracker,
        abstain_steps=abstain_steps,
        ledger_directory=directory / "usage",
        client=client,
    )
    replayer = build_replayer(
        settings,
        launcher=launcher,
        artifacts=artifacts,
        events=tracker,
        environ={secret_variable_name(SecretName(name)): value for name, value in secrets.items()},
        egress=local_policy(inputs.values()),
        resolver=resolver,
        scrubber=SecretScrubber(),
        model=rung,
    )
    run = await replayer.run(workflow, inputs)
    return LabelledRun(run, tuple(tracker.events), tuple(checks), tracker)
