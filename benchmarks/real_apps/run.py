"""Scoring one release through every system, against a person's approved labels (ADR 0014).

The pair's value is that nobody manufactured the change: a workflow recorded on release A is
replayed on release B, and what counts as correct there is a person's label, frozen by an approval
of the labels' digest. Scoring refuses to start without that approval, and refuses settings other
than the product's defaults, so a number here cannot be obtained by tuning Mendwork to the release.

Each system gets a freshly started, freshly seeded container: the task's last step creates an
issue, which no later run can undo, so sharing one instance would show every system a different
application.
"""

import platform
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Final

import httpx
from playwright.async_api import Browser, async_playwright

from benchmarks.baselines.script_runner import ScriptRunner
from benchmarks.chaos.bench import SOURCE_TREES, settings_overrides, source_digest
from benchmarks.chaos.local_egress import local_policy
from benchmarks.chaos.systems import (
    LADDER_MODEL,
    describe,
    is_script,
    model_mode,
    script_kind,
)
from benchmarks.real_apps.instances import Release, running
from benchmarks.real_apps.labels import LabelApproval, PairLabels, approved
from benchmarks.real_apps.replay import run_with_labels
from benchmarks.real_apps.truth import LabelProbes
from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.system.resolver import SystemHostResolver
from mendwork.apps.cli.wiring import egress_enforcement, model_client, session_options
from mendwork.engine.benchmark.cells import (
    CellResult,
    RealAppCell,
    RunFailure,
    StepTiming,
    UsageMeasurement,
)
from mendwork.engine.benchmark.observe import ActionProbe, observe_run, run_failure_of
from mendwork.engine.benchmark.outcomes import classify_step
from mendwork.engine.benchmark.results import (
    BenchmarkResults,
    ModelProvenance,
    Provenance,
    build_section,
)
from mendwork.engine.benchmark.truth import Expectation, StepObservation, StepTruth
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.healing.prompt import PROMPT_VERSION
from mendwork.settings import Settings

REAL_APP: Final = "real_app"
ALLOWED_SETTINGS: Final = frozenset(
    {
        "trace_on_failure",
        "model_provider",
        "model_name",
        # Where files are written cannot change which element a step finds.
        "artifacts_dir",
        "workflow_store_dir",
    }
)
"""Settings a real-app run may differ in: none of them can change what a step resolves to."""

CellProgress = Callable[[CellResult], None]
InputsFor = Callable[[str], Mapping[str, str]]


class ScoringRefusedError(RuntimeError):
    """The release may not be scored: no approval, changed labels, or non-default settings."""


@dataclass(frozen=True, slots=True)
class RealAppPlan:
    """One release of one pair, and how to run the recorded task on it."""

    pair: str
    recorded_on: Release
    """The release the workflow was recorded on, kept so provenance names both images."""
    release: Release
    """The release being replayed and scored."""
    workflow: WorkflowVersion
    workflow_path: Path
    labels: PairLabels
    approval: LabelApproval | None
    systems: tuple[str, ...]
    inputs_for: InputsFor
    secrets: Mapping[str, str]
    notes: tuple[str, ...] = ()
    """What the operator recorded about the conditions this run was measured under."""


def check_settings(settings: Settings) -> None:
    """A real-app run uses the product's defaults; anything else could be tuning to the release."""
    changed = sorted(set(settings_overrides(settings)) - ALLOWED_SETTINGS)
    if changed:
        raise ScoringRefusedError(
            f"a real-app run uses the defaults; these settings differ: {changed}"
        )


def check_plan(plan: RealAppPlan) -> LabelApproval:
    """The approval for exactly these labels, for exactly this release and workflow."""
    if plan.labels.release != plan.release.label or plan.labels.pair != plan.pair:
        raise ScoringRefusedError(
            f"the labels are for {plan.labels.pair} {plan.labels.release}, "
            f"not {plan.pair} {plan.release.label}"
        )
    if plan.labels.workflow_id != plan.workflow.workflow_id:
        raise ScoringRefusedError(
            f"the labels are for {plan.labels.workflow_id}, not {plan.workflow.workflow_id}"
        )
    labelled = {step.step_id for step in plan.labels.steps}
    acted = {str(step.id) for step in plan.workflow.steps if step.action is not ActionType.NAVIGATE}
    if labelled != acted:
        raise ScoringRefusedError(
            f"every step that acts needs a label: {sorted(acted - labelled)} unlabelled, "
            f"{sorted(labelled - acted)} labelled but not in the workflow"
        )
    return approved(plan.labels, plan.approval)


async def score(
    plan: RealAppPlan,
    settings: Settings,
    *,
    generated_at: datetime,
    repository: Path,
    progress: CellProgress | None = None,
) -> BenchmarkResults:
    """Run every system against its own instance of the release, and return the results."""
    approval = check_plan(plan)
    check_settings(settings)
    digests = source_digests(plan, repository)
    truths = plan.labels.truths()
    probes = LabelProbes(plan.labels.selectors())
    cells: list[CellResult] = []
    with tempfile.TemporaryDirectory(prefix="mendwork-real-app-") as scratch:
        async with async_playwright() as playwright, model_client(settings) as client:
            if LADDER_MODEL in plan.systems and client is None:
                raise ScoringRefusedError(
                    "the ladder_model system needs a model: set MENDWORK_MODEL_PROVIDER and "
                    "MENDWORK_MODEL_NAME"
                )
            browser = await playwright.chromium.launch()
            browser_version = browser.version
            try:
                for number, system in enumerate(plan.systems):
                    # A fresh container per system: creating the issue cannot be undone.
                    async with running(plan.release) as instance:
                        cell = await _cell(
                            browser,
                            plan,
                            system,
                            plan.inputs_for(instance.base_url),
                            probes,
                            truths,
                            settings,
                            client if system == LADDER_MODEL else None,
                            Path(scratch) / str(number),
                        )
                    cells.append(cell)
                    if progress is not None:
                        progress(cell)
            finally:
                await browser.close()
    return BenchmarkResults(
        provenance=_provenance(plan, settings, generated_at, digests, browser_version, approval),
        systems=tuple(
            describe(system, model=settings.model_name if system == LADDER_MODEL else None)
            for system in plan.systems
        ),
        sections=(
            build_section(
                REAL_APP,
                f"{plan.pair.title()}: recorded on {plan.recorded_on.label}, replayed on "
                f"{plan.release.label}",
                _description(plan),
                tuple(cells),
                plan.systems,
            ),
        ),
    )


def source_digests(plan: RealAppPlan, repository: Path) -> dict[str, str]:
    """Digests of the code, the workflow, the labels, and the image a run executes."""
    written = (repository / "benchmarks" / "results",)
    digests = {
        name: source_digest(repository / name, suffixes, exclude=written)
        for name, suffixes in SOURCE_TREES
    }
    digests[f"workflow:{plan.workflow.workflow_id}"] = source_digest(plan.workflow_path, (".yaml",))
    digests[f"labels:{plan.pair} {plan.release.label}"] = plan.labels.digest
    digests[f"image:{plan.pair} {plan.recorded_on.label} (recorded on)"] = plan.recorded_on.image
    digests[f"image:{plan.pair} {plan.release.label} (replayed on)"] = plan.release.image
    return digests


def _description(plan: RealAppPlan) -> str:
    return (
        f"A workflow recorded on {plan.pair} {plan.recorded_on.label} and replayed on "
        f"{plan.release.label}, each our own container pinned by digest. What counts as correct is "
        f"a person's label for every acting step of {plan.release.label}, written from that "
        f"release's own pages before Mendwork ran on it, approved against the screenshots of a "
        f"walk that performed the task with the labels alone, and frozen by the labels' digest. "
        f"Every system gets a freshly seeded instance, because the task ends by creating an issue."
    )


def _provenance(
    plan: RealAppPlan,
    settings: Settings,
    generated_at: datetime,
    digests: Mapping[str, str],
    browser_version: str,
    approval: LabelApproval,
) -> Provenance:
    models = (
        (
            ModelProvenance(
                system=LADDER_MODEL,
                provider=settings.model_provider.value,
                model=settings.model_name or "unnamed",
                prompt_version=PROMPT_VERSION,
            ),
        )
        if LADDER_MODEL in plan.systems
        else ()
    )
    return Provenance(
        generated_at=generated_at,
        mendwork_version=package_version("mendwork"),
        platform=platform.platform(),
        python=platform.python_version(),
        browser=f"chromium {browser_version}",
        concurrency=1,
        step_timeout_ms=settings.step_timeout_ms,
        workflows=(plan.workflow.workflow_id,),
        source_digests=digests,
        settings_overrides=settings_overrides(settings),
        models=models,
        notes=(
            *plan.notes,
            f"The {plan.release.label} labels were written before Mendwork ran on that release, "
            f"and approved on {approval.approved_at:%Y-%m-%d} by {approval.approved_by} against "
            f"the {len(approval.screenshots)} screenshots of the labels walk.",
            "The task's last step creates an issue, which cannot be undone. Its recorded risk was "
            "raised to irreversible, so a heal there stops for a person instead of acting.",
        ),
    )


async def _cell(
    browser: Browser,
    plan: RealAppPlan,
    system: str,
    inputs: Mapping[str, str],
    probes: LabelProbes,
    truths: Mapping[str, StepTruth],
    settings: Settings,
    client: httpx.AsyncClient | None,
    directory: Path,
) -> CellResult:
    cell = RealAppCell(
        pair=f"{plan.pair} {plan.recorded_on.label} to {plan.release.label}",
        workflow_id=plan.workflow.workflow_id,
        system=system,
    )
    targets = {step_id: step_id for step_id in truths}
    if is_script(system):
        launcher = await PlaywrightLauncher.create(
            browser, session_options(settings), egress_enforcement(settings, SystemHostResolver())
        )
        script = await ScriptRunner(
            launcher,
            settings,
            kind=script_kind(system),
            targets=targets,
            inputs=inputs,
            secrets=plan.secrets,
            egress=local_policy(inputs.values()),
            signed_in=False,
            matched=probes.matched,
            available=probes.available,
            wrong_count=probes.wrong_count,
        ).run(plan.workflow)
        return _result(
            cell,
            truths,
            script.observations,
            script.status,
            0,
            UsageMeasurement(),
            script.duration_ms,
            script.failure,
        )
    replay = await run_with_labels(
        browser,
        plan.workflow,
        inputs,
        plan.secrets,
        targets,
        probes,
        directory,
        settings=settings,
        model=model_mode(system),
        abstain_steps=frozenset(
            step for step, truth in truths.items() if truth.expectation is Expectation.ABSTAIN
        ),
        client=client,
    )
    observations = observe_run(
        replay.run,
        plan.workflow,
        replay.events,
        [
            ActionProbe(
                step_id=check.step_id,
                action=ActionType(check.action),
                event_position=check.event_position,
                matched_keys=check.matched_keys,
                ground_truth_known=check.ground_truth_known,
            )
            for check in replay.checks
            if check.step_id is not None
        ],
        truths,
        available=replay.tracker.available,
        # A real application counts no wrong actions of its own; the labels are the only witness.
        page_wrong={},
    )
    usage = replay.run.model_usage
    return _result(
        cell,
        truths,
        observations,
        replay.run.status.value,
        usage.calls,
        UsageMeasurement(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=usage.latency_ms,
            cost_usd=usage.estimated_cost_usd,
            unpriced_calls=usage.unpriced_calls,
        ),
        replay.run.duration_ms,
        run_failure_of(replay.run, plan.workflow),
    )


def _result(
    cell: RealAppCell,
    truths: Mapping[str, StepTruth],
    observations: Sequence[StepObservation],
    status: str,
    model_calls: int,
    usage: UsageMeasurement,
    duration_ms: int | None,
    failure: RunFailure | None,
) -> CellResult:
    return CellResult(
        cell=cell,
        run_status=status,
        run_failure=failure,
        steps=tuple(classify_step(truths[item.step_id], item) for item in observations),
        model_calls=model_calls,
        timings=tuple(
            StepTiming(step_id=item.step_id, duration_ms=item.duration_ms) for item in observations
        ),
        usage=usage,
        duration_ms=duration_ms,
    )
