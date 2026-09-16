"""The chaos benchmark: every workflow at every level and seed, through every system (ADR 0014).

Ground truth comes from the portal's ``window.__chaos``, read by benchmark code only. For each
(level, seed) the benchmark first reads which changes every page received; each cell then runs one
workflow through one system in its own browser context, with every action checked against the
portal at the moment it happens, and is classified by the engine's pure rules. The script systems
run through ``benchmarks.baselines``; the ladder systems run the product exactly as ``mendwork run``
wires it.

The optional single-mutation section runs the heal fixture suite's cases (one change per page)
through the same systems.
"""

import asyncio
import hashlib
import platform
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Final

import httpx
from playwright.async_api import Browser, Page, async_playwright
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

from benchmarks.baselines.script_runner import ScriptRunner
from benchmarks.chaos.ground_truth import SIGNED_IN_SCRIPT
from benchmarks.chaos.heal_cases import (
    DEMO_PASSWORD,
    ExampleWorkflow,
    HealCase,
    build_cases,
    case_settings,
    case_workflow,
    run_with_ground_truth,
)
from benchmarks.chaos.heal_pairs import ABSTAIN_TABLE_PATH, PAGE_PATHS, PORTAL_ROOT, load_table
from benchmarks.chaos.local_egress import local_policy
from benchmarks.chaos.systems import (
    LADDER_MODEL,
    describe,
    is_script,
    model_mode,
    script_kind,
)
from benchmarks.chaos.workflow_targets import (
    WORKFLOW_TARGETS_DIR,
    WorkflowTargets,
    load_workflow_targets,
)
from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.system.resolver import SystemHostResolver
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.wiring import egress_enforcement, model_client, session_options
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.benchmark.cells import (
    CellResult,
    ChaosCell,
    MutationCaseCell,
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
    Section,
    build_section,
)
from mendwork.engine.benchmark.truth import (
    AppliedMutation,
    Expectation,
    MutationCategory,
    StepObservation,
    StepTruth,
    step_truths,
)
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.healing.prompt import PROMPT_VERSION
from mendwork.settings import Settings

READY_TIMEOUT_MS: Final = 5_000
CHAOS_GRID: Final = "chaos_grid"
SINGLE_MUTATIONS: Final = "single_mutations"
_SENSITIVE_WORDS: Final = ("key", "secret", "token", "password")
SOURCE_TREES: Final = (
    ("src/mendwork", (".py", ".js", ".css")),
    ("chaos-portal", (".html", ".js", ".css")),
    ("benchmarks", (".py", ".json")),
)
CellProgress = Callable[[CellResult], None]


class BenchmarkSetupError(ValueError):
    """The benchmark cannot start: a workflow, its target map, or the portal is not usable."""


class _Applied(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str
    category: MutationCategory
    target_key: str | None = Field(default=None, alias="targetKey")


_APPLIED: Final[TypeAdapter[list[_Applied]]] = TypeAdapter(list[_Applied])


@dataclass(frozen=True, slots=True)
class BenchWorkflow:
    """A workflow file and the ground-truth map that lets the benchmark run it on the portal."""

    version: WorkflowVersion
    targets: WorkflowTargets
    path: Path


@dataclass(frozen=True, slots=True)
class ChaosBenchPlan:
    """What one benchmark run covers."""

    workflows: tuple[BenchWorkflow, ...]
    levels: tuple[int, ...]
    seeds: tuple[int, ...]
    systems: tuple[str, ...]
    concurrency: int
    single_mutations: bool
    examples: Mapping[str, ExampleWorkflow] | None = None
    """The committed examples and their maps, needed only for the single-mutation cases."""
    notes: tuple[str, ...] = ()
    """What the operator recorded about the conditions this run was measured under.

    Latency depends on what else the machine was doing, and only the person at the keyboard knows
    that, so it is passed in rather than guessed at.
    """


@dataclass(frozen=True, slots=True)
class _Job:
    cell: ChaosCell | MutationCaseCell
    workflow: WorkflowVersion
    targets: Mapping[str, str]
    inputs: Mapping[str, str]
    secrets: Mapping[str, str]
    truths: Mapping[str, StepTruth]
    settings: Settings
    signed_in: bool


def load_bench_workflows(
    directory: Path, *, targets_dir: Path = WORKFLOW_TARGETS_DIR, max_bytes: int
) -> tuple[BenchWorkflow, ...]:
    """Every workflow file in ``directory`` with its target map; one without a map is refused."""
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise BenchmarkSetupError(f"no workflow files (*.yaml) in {directory}")
    codec = WorkflowYamlCodec(max_bytes=max_bytes)
    loaded: list[BenchWorkflow] = []
    for path in paths:
        version = codec.decode(path.read_bytes(), source=str(path))
        map_path = targets_dir / f"{version.workflow_id}.json"
        if not map_path.is_file():
            raise BenchmarkSetupError(
                f"{path.name}: no ground-truth target map at {map_path}; the chaos benchmark "
                "needs one to know which control each step acts on"
            )
        targets = load_workflow_targets(version.workflow_id, targets_dir)
        step_ids = {str(step.id) for step in version.steps}
        unknown = sorted(set(targets.targets) - step_ids)
        if unknown:
            raise BenchmarkSetupError(f"{map_path.name} maps steps {path.name} lacks: {unknown}")
        loaded.append(BenchWorkflow(version, targets, path))
    return tuple(loaded)


async def run_chaos_benchmark(
    plan: ChaosBenchPlan,
    settings: Settings,
    *,
    generated_at: datetime,
    repository: Path,
    progress: CellProgress | None = None,
) -> BenchmarkResults:
    """Run every cell of the plan and return the results document."""
    digests = await asyncio.to_thread(source_digests, plan, repository)
    with (
        tempfile.TemporaryDirectory(prefix="mendwork-bench-") as scratch,
        PortalServer(PORTAL_ROOT, host="127.0.0.1", port=0) as server,
    ):
        async with async_playwright() as playwright, model_client(settings) as client:
            if LADDER_MODEL in plan.systems and client is None:
                raise BenchmarkSetupError(
                    "the ladder_model system needs a model: set MENDWORK_MODEL_PROVIDER and "
                    "MENDWORK_MODEL_NAME"
                )
            browser = await playwright.chromium.launch()
            try:
                grid = await _grid_jobs(browser, server.url, plan, settings)
                cases = _case_jobs(server.url, plan) if plan.single_mutations else []
                finished = await _run_jobs(
                    browser, [*grid, *cases], plan, client, Path(scratch), progress
                )
                browser_version = browser.version
            finally:
                await browser.close()
    sections = [_section(CHAOS_GRID, plan, finished, _grid_description(plan))]
    if plan.single_mutations:
        sections.append(_section(SINGLE_MUTATIONS, plan, finished, _cases_description()))
    return BenchmarkResults(
        provenance=_provenance(plan, settings, generated_at, digests, browser_version),
        systems=tuple(
            describe(system, model=settings.model_name if system == LADDER_MODEL else None)
            for system in plan.systems
        ),
        sections=tuple(sections),
    )


async def applied_mutations(
    browser: Browser, portal: str, seed: int, level: int
) -> tuple[AppliedMutation, ...]:
    """Every change the portal applies on each page for a seed and level, by ground truth."""
    found: list[AppliedMutation] = []
    for page_id, path in PAGE_PATHS.items():
        context = await browser.new_context()
        try:
            if page_id != "login":
                await context.add_init_script(script=SIGNED_IN_SCRIPT)
            page = await context.new_page()
            found.extend(await _page_applied(page, f"{portal}{path}?seed={seed}&level={level}"))
        finally:
            await context.close()
    return tuple(found)


async def _page_applied(page: Page, url: str) -> list[AppliedMutation]:
    await page.goto(url)
    await page.wait_for_function("() => window.__chaos?.ready === true", timeout=READY_TIMEOUT_MS)
    problem = await page.evaluate("() => window.__chaos.error")
    if problem is not None:
        raise BenchmarkSetupError(f"the portal refused {url}: {problem}")
    entries = _APPLIED.validate_python(await page.evaluate("() => window.__chaos.applied"))
    return [
        AppliedMutation(mutation=entry.id, category=entry.category, target_key=entry.target_key)
        for entry in entries
    ]


async def _grid_jobs(
    browser: Browser, portal: str, plan: ChaosBenchPlan, settings: Settings
) -> list[_Job]:
    jobs: list[_Job] = []
    for level in plan.levels:
        for seed in plan.seeds:
            applied = await applied_mutations(browser, portal, seed, level)
            for workflow in plan.workflows:
                truths = step_truths(workflow.targets.targets, applied)
                for system in plan.systems:
                    jobs.append(
                        _Job(
                            cell=ChaosCell(
                                workflow_id=workflow.version.workflow_id,
                                level=level,
                                seed=seed,
                                system=system,
                            ),
                            workflow=workflow.version,
                            targets=workflow.targets.targets,
                            inputs=workflow.targets.run_inputs(
                                portal=portal, seed=seed, level=level
                            ),
                            secrets=workflow.targets.secrets,
                            truths=truths,
                            settings=settings,
                            signed_in=False,
                        )
                    )
    return jobs


def _case_jobs(portal: str, plan: ChaosBenchPlan) -> list[_Job]:
    examples = plan.examples
    if examples is None:
        raise BenchmarkSetupError("single-mutation cases need the committed example workflows")
    cases = build_cases(examples, load_table(), load_table(ABSTAIN_TABLE_PATH))
    jobs: list[_Job] = []
    for case in cases:
        version, inputs = case_workflow(case, examples[case.workflow_id], portal)
        present = {str(step.id) for step in version.steps}
        targets = {
            step: key for step, key in examples[case.workflow_id].targets.items() if step in present
        }
        truths = step_truths(targets, [_case_mutation(case)])
        for system in plan.systems:
            jobs.append(
                _Job(
                    cell=MutationCaseCell(
                        case_id=case.id,
                        mutation=case.mutation,
                        category=MutationCategory(case.category),
                        workflow_id=case.workflow_id,
                        system=system,
                    ),
                    workflow=version,
                    targets=targets,
                    inputs=inputs,
                    secrets={"portal_password": DEMO_PASSWORD},
                    truths=truths,
                    settings=case_settings(case),
                    signed_in=case.page != "login",
                )
            )
    return jobs


def _case_mutation(case: HealCase) -> AppliedMutation:
    return AppliedMutation(
        mutation=case.mutation, category=MutationCategory(case.category), target_key=case.target
    )


async def _run_jobs(
    browser: Browser,
    jobs: Sequence[_Job],
    plan: ChaosBenchPlan,
    client: httpx.AsyncClient | None,
    scratch: Path,
    progress: CellProgress | None,
) -> list[CellResult]:
    limit = asyncio.Semaphore(plan.concurrency)
    # A local model answers one request at a time; queued calls would spend the heal budget waiting.
    model_lock = asyncio.Semaphore(1)

    async def bounded(number: int, job: _Job) -> CellResult:
        if job.cell.system == LADDER_MODEL:
            async with model_lock, limit:
                result = await _run_job(browser, job, client, scratch / str(number))
        else:
            async with limit:
                result = await _run_job(browser, job, client, scratch / str(number))
        if progress is not None:
            progress(result)
        return result

    return list(await asyncio.gather(*(bounded(number, job) for number, job in enumerate(jobs))))


async def _run_job(
    browser: Browser, job: _Job, client: httpx.AsyncClient | None, directory: Path
) -> CellResult:
    system = job.cell.system
    if is_script(system):
        launcher = await PlaywrightLauncher.create(
            browser,
            session_options(job.settings),
            egress_enforcement(job.settings, SystemHostResolver()),
        )
        script = await ScriptRunner(
            launcher,
            job.settings,
            kind=script_kind(system),
            targets=job.targets,
            inputs=job.inputs,
            secrets=job.secrets,
            egress=local_policy(job.inputs.values()),
            signed_in=job.signed_in,
        ).run(job.workflow)
        return _cell(
            job,
            script.observations,
            script.status,
            0,
            UsageMeasurement(),
            script.duration_ms,
            script.failure,
        )
    replay = await run_with_ground_truth(
        browser,
        job.workflow,
        job.inputs,
        job.targets,
        directory,
        settings=job.settings,
        signed_in=job.signed_in,
        model=model_mode(system),
        abstain_steps=frozenset(
            step for step, truth in job.truths.items() if truth.expectation is Expectation.ABSTAIN
        ),
        client=client if system == LADDER_MODEL else None,
    )
    probes = [
        ActionProbe(
            step_id=check.step_id,
            action=ActionType(check.action),
            event_position=check.event_position,
            matched_keys=check.matched_keys,
            ground_truth_known=check.ground_truth_known,
        )
        for check in replay.checks
        if check.step_id is not None
    ]
    observations = observe_run(
        replay.run,
        job.workflow,
        replay.events,
        probes,
        job.truths,
        available=replay.tracker.available,
        page_wrong=replay.tracker.page_wrong,
    )
    usage = replay.run.model_usage
    return _cell(
        job,
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
        run_failure_of(replay.run, job.workflow),
    )


def _cell(
    job: _Job,
    observations: Sequence[StepObservation],
    status: str,
    model_calls: int,
    usage: UsageMeasurement,
    duration_ms: int | None,
    failure: RunFailure | None,
) -> CellResult:
    return CellResult(
        cell=job.cell,
        run_status=status,
        run_failure=failure,
        steps=tuple(classify_step(job.truths[item.step_id], item) for item in observations),
        model_calls=model_calls,
        timings=tuple(
            StepTiming(step_id=item.step_id, duration_ms=item.duration_ms) for item in observations
        ),
        usage=usage,
        duration_ms=duration_ms,
    )


def _section(
    section_id: str, plan: ChaosBenchPlan, finished: Iterable[CellResult], description: str
) -> Section:
    kind = "chaos" if section_id == CHAOS_GRID else "single_mutation"
    cells = tuple(cell for cell in finished if cell.cell.type == kind)
    title = "Chaos grid" if section_id == CHAOS_GRID else "Single mutations"
    return build_section(section_id, title, description, cells, plan.systems)


def _grid_description(plan: ChaosBenchPlan) -> str:
    levels = ", ".join(str(level) for level in plan.levels)
    seeds = plan.seeds
    span = f"{seeds[0]} to {seeds[-1]}" if len(seeds) > 1 else str(seeds[0])
    workflows = ", ".join(workflow.version.workflow_id for workflow in plan.workflows)
    return (
        f"{len(plan.workflows)} workflow{'' if len(plan.workflows) == 1 else 's'} ({workflows}) "
        f"on the chaos portal at level{'' if len(plan.levels) == 1 else 's'} {levels}, "
        f"{len(seeds)} seed{'' if len(seeds) == 1 else 's'} each ({span}), every action checked "
        "against the portal's ground "
        "truth at the moment it happened."
    )


def _cases_description() -> str:
    return (
        "The heal fixture suite's cases through every system: each heal-expected and "
        "abstain-expected change on a control the example workflows act on, and the cookie "
        "banner on each page they visit, one change per page. Removed-control cases use a "
        "1,500 ms step timeout, as the suite does."
    )


def source_digests(plan: ChaosBenchPlan, repository: Path) -> dict[str, str]:
    """Digests of the code and workflows a run executes, read before its first cell runs."""
    written = (repository / "benchmarks" / "results",)
    digests = {
        name: source_digest(repository / name, suffixes, exclude=written)
        for name, suffixes in SOURCE_TREES
    }
    for workflow in plan.workflows:
        digests[f"workflow:{workflow.version.workflow_id}"] = source_digest(
            workflow.path, (".yaml",)
        )
    return digests


def _provenance(
    plan: ChaosBenchPlan,
    settings: Settings,
    generated_at: datetime,
    digests: Mapping[str, str],
    browser_version: str,
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
        concurrency=plan.concurrency,
        step_timeout_ms=settings.step_timeout_ms,
        levels=plan.levels,
        seeds=plan.seeds,
        workflows=tuple(workflow.version.workflow_id for workflow in plan.workflows),
        source_digests=digests,
        settings_overrides=settings_overrides(settings),
        models=models,
        notes=plan.notes,
    )


def settings_overrides(settings: Settings) -> dict[str, object]:
    """Every setting that differs from its default, secrets and credentials left out."""
    overrides: dict[str, object] = {}
    dumped = settings.model_dump(mode="json")
    for name, field in type(settings).model_fields.items():
        value = getattr(settings, name)
        if isinstance(value, SecretStr) or any(word in name for word in _SENSITIVE_WORDS):
            continue
        if value != field.get_default(call_default_factory=True):
            overrides[name] = dumped[name]
    return dict(sorted(overrides.items()))


def source_digest(path: Path, suffixes: Sequence[str], *, exclude: Sequence[Path] = ()) -> str:
    """The SHA-256 of a file, or of every matching file under a directory, by relative path.

    Files under ``exclude`` are left out, so a digest of the harness never includes the results the
    harness itself wrote.
    """
    files = (
        [path]
        if path.is_file()
        else sorted(
            item
            for item in path.rglob("*")
            if item.is_file()
            and item.suffix in suffixes
            and "__pycache__" not in item.parts
            and not any(item.is_relative_to(excluded) for excluded in exclude)
        )
    )
    digest = hashlib.sha256()
    for item in files:
        relative = item.name if item == path else item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8") + b"\0" + item.read_bytes() + b"\0")
    return f"sha256:{digest.hexdigest()}"
