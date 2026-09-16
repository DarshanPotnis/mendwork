"""``mendwork bench``: measure Mendwork against script baselines and write the scorecard (ADR 0014).

A composition root. The harness lives in the repository's ``benchmarks`` package, which reads the
chaos portal's ground truth and is never part of the installed product, so it is imported only when
a benchmark runs; from an installed wheel the command says where the benchmark runs instead.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Final, NoReturn

import typer
from pydantic import ValidationError

from mendwork.adapters.benchmark_fs.files import read_results, write_results
from mendwork.adapters.scorecard_html.writer import write_scorecard
from mendwork.adapters.system.clock import SystemClock
from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.engine.benchmark.cells import CellResult, cell_key
from mendwork.engine.benchmark.results import BenchmarkResults, gate_failures
from mendwork.engine.errors import ArtifactStoreUnavailable, WorkflowValidationError
from mendwork.engine.reporting.scorecard_view import fraction
from mendwork.settings import Settings

HARNESS_MISSING: Final = (
    "the benchmark harness is not installed here: `mendwork bench` runs from a source "
    "checkout of Mendwork (uv run mendwork bench ...), where benchmarks/ is importable"
)
RESULTS_FILE: Final = "chaos-results.json"
REAL_APP_FILE: Final = "real-app-results.json"
SCORECARD_FILE: Final = "scorecard.html"
LEVELS: Final = range(6)

bench_app = typer.Typer(
    name="bench",
    help="Measure Mendwork against script baselines, with ground truth at every action.",
    no_args_is_help=True,
)


@bench_app.command("chaos")
def chaos(
    workflows: Annotated[
        Path,
        typer.Option("--workflows", file_okay=False, exists=True, help="Workflow files to run."),
    ],
    level: Annotated[list[int], typer.Option("--level", help="A chaos level; repeat for several.")],
    seeds: Annotated[int, typer.Option("--seeds", min=1, help="How many seeds at each level.")],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, help="Where to write results and scorecard."),
    ],
    seed_start: Annotated[int, typer.Option("--seed-start", min=0, help="The first seed.")] = 0,
    system: Annotated[
        list[str] | None,
        typer.Option("--system", help="A system to run; repeat for several (default: four)."),
    ] = None,
    concurrency: Annotated[int, typer.Option("--concurrency", min=1, max=16)] = 4,
    step_timeout_ms: Annotated[
        int | None, typer.Option("--step-timeout-ms", min=1, help="Override the step timeout.")
    ] = None,
    single_mutations: Annotated[
        bool,
        typer.Option(
            "--single-mutations/--no-single-mutations",
            help="Also run the heal fixture suite's one-change cases through every system.",
        ),
    ] = False,
    include: Annotated[
        list[Path] | None,
        typer.Option(
            "--include",
            dir_okay=False,
            exists=True,
            help="Another results file to show after this run's, such as a real-app pair's.",
        ),
    ] = None,
    note: Annotated[
        list[str] | None,
        typer.Option(
            "--note",
            help="What the machine was doing during the run; recorded in the provenance.",
        ),
    ] = None,
    gate: Annotated[
        bool, typer.Option("--gate", help="Exit 1 if a gated system acted on a wrong element.")
    ] = False,
) -> None:
    """Run workflows on the chaos portal across levels and seeds, through every system."""
    try:
        import benchmarks
        from benchmarks.chaos.bench import (
            BenchmarkSetupError,
            ChaosBenchPlan,
            load_bench_workflows,
            run_chaos_benchmark,
        )
        from benchmarks.chaos.heal_cases import load_workflows
        from benchmarks.chaos.systems import DEFAULT_SYSTEMS, SYSTEM_IDS
    except ModuleNotFoundError as error:
        if error.name is None or not error.name.startswith("benchmarks"):
            raise
        _stop(HARNESS_MISSING, ExitCode.INVALID)
    chosen = tuple(system or DEFAULT_SYSTEMS)
    unknown = sorted(set(chosen) - set(SYSTEM_IDS))
    if unknown or len(set(chosen)) != len(chosen):
        _stop(f"systems must be distinct and among {', '.join(SYSTEM_IDS)}", ExitCode.INVALID)
    if any(value not in LEVELS for value in level):
        _stop("levels are 0 to 5", ExitCode.INVALID)
    update: dict[str, object] = {"trace_on_failure": False}
    if step_timeout_ms is not None:
        update["step_timeout_ms"] = step_timeout_ms
    settings = Settings().model_copy(update=update)
    package = benchmarks.__file__
    if package is None:
        _stop(HARNESS_MISSING, ExitCode.INVALID)
    try:
        alongside = asyncio.run(_read_all(include or []))
        plan = ChaosBenchPlan(
            workflows=load_bench_workflows(workflows, max_bytes=settings.workflow_max_bytes),
            levels=tuple(sorted(set(level))),
            seeds=tuple(range(seed_start, seed_start + seeds)),
            systems=chosen,
            concurrency=concurrency,
            single_mutations=single_mutations,
            examples=load_workflows() if single_mutations else None,
            notes=tuple(note or ()),
        )
        results = asyncio.run(
            run_chaos_benchmark(
                plan,
                settings,
                generated_at=SystemClock().now(),
                repository=Path(package).resolve().parents[1],
                progress=_progress,
            )
        )
    except (BenchmarkSetupError, WorkflowValidationError, ValidationError) as error:
        _stop(str(error), ExitCode.INVALID)
    except ArtifactStoreUnavailable as error:
        _stop(error.message, ExitCode.INFRASTRUCTURE)
    _write(output / RESULTS_FILE, output / SCORECARD_FILE, results, alongside)
    _summarize(results)
    failures = gate_failures(results)
    for failure in failures:
        typer.echo(f"WRONG ACTION: {failure}", err=True)
    if gate and failures:
        raise typer.Exit(code=ExitCode.STEP_FAILED)


@bench_app.command("real-app")
def real_app(
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, help="Where to write the results file."),
    ],
    system: Annotated[
        list[str] | None,
        typer.Option("--system", help="A system to run; repeat for several (default: four)."),
    ] = None,
    note: Annotated[
        list[str] | None,
        typer.Option(
            "--note",
            help="What the machine was doing during the run; recorded in the provenance.",
        ),
    ] = None,
    gate: Annotated[
        bool, typer.Option("--gate", help="Exit 1 if a gated system acted on a wrong element.")
    ] = False,
) -> None:
    """Replay a workflow recorded on one release of a real application on the next one.

    Ground truth is a person's label for every acting step of the later release, frozen by an
    approval of the labels' digest: scoring stops rather than measure labels nobody approved.
    """
    try:
        import benchmarks
        from benchmarks.chaos.systems import DEFAULT_SYSTEMS, SYSTEM_IDS
        from benchmarks.real_apps.gitea.pair import plan as gitea_plan
        from benchmarks.real_apps.instances import InstanceError
        from benchmarks.real_apps.labels import LabelsNotApprovedError
        from benchmarks.real_apps.run import ScoringRefusedError, score
    except ModuleNotFoundError as error:
        if error.name is None or not error.name.startswith("benchmarks"):
            raise
        _stop(HARNESS_MISSING, ExitCode.INVALID)
    chosen = tuple(system or DEFAULT_SYSTEMS)
    unknown = sorted(set(chosen) - set(SYSTEM_IDS))
    if unknown or len(set(chosen)) != len(chosen):
        _stop(f"systems must be distinct and among {', '.join(SYSTEM_IDS)}", ExitCode.INVALID)
    settings = Settings().model_copy(update={"trace_on_failure": False})
    package = benchmarks.__file__
    if package is None:
        _stop(HARNESS_MISSING, ExitCode.INVALID)
    try:
        results = asyncio.run(
            score(
                gitea_plan(chosen, max_bytes=settings.workflow_max_bytes, notes=tuple(note or ())),
                settings,
                generated_at=SystemClock().now(),
                repository=Path(package).resolve().parents[1],
                progress=_progress,
            )
        )
    except (LabelsNotApprovedError, ScoringRefusedError) as error:
        _stop(str(error), ExitCode.INVALID)
    except (WorkflowValidationError, ValidationError) as error:
        _stop(str(error), ExitCode.INVALID)
    except InstanceError as error:
        _stop(str(error), ExitCode.INFRASTRUCTURE)
    except ArtifactStoreUnavailable as error:
        _stop(error.message, ExitCode.INFRASTRUCTURE)
    _write_results_only(output / REAL_APP_FILE, results)
    _summarize(results)
    failures = gate_failures(results)
    for failure in failures:
        typer.echo(f"WRONG ACTION: {failure}", err=True)
    if gate and failures:
        raise typer.Exit(code=ExitCode.STEP_FAILED)


@bench_app.command("scorecard")
def scorecard(
    results: Annotated[
        list[Path],
        typer.Option("--results", dir_okay=False, exists=True, help="Results files, in order."),
    ],
    output: Annotated[Path, typer.Option("--output", dir_okay=False, help="The HTML to write.")],
) -> None:
    """Write one scorecard from results files, shown in the order given."""
    try:
        documents = asyncio.run(_read_all(results))
        asyncio.run(write_scorecard(output, documents))
    except ValidationError as error:
        _stop(f"not a valid benchmark results document: {error}", ExitCode.INVALID)
    except ArtifactStoreUnavailable as error:
        _stop(error.message, ExitCode.INFRASTRUCTURE)
    typer.echo(f"wrote {output}")


async def _read_all(paths: Sequence[Path]) -> list[BenchmarkResults]:
    return [await read_results(path) for path in paths]


def _write(
    results_path: Path,
    scorecard_path: Path,
    results: BenchmarkResults,
    alongside: Sequence[BenchmarkResults],
) -> None:
    async def write() -> None:
        await write_results(results_path, results)
        # This run leads: its headline is the claim the scorecard is built on, and a smaller
        # suite shown beside it, such as a real-app pair, follows in its own section.
        await write_scorecard(scorecard_path, [results, *alongside])

    try:
        asyncio.run(write())
    except ArtifactStoreUnavailable as error:
        _stop(error.message, ExitCode.INFRASTRUCTURE)
    typer.echo(f"wrote {results_path}")
    typer.echo(f"wrote {scorecard_path}")


def _write_results_only(path: Path, results: BenchmarkResults) -> None:
    try:
        asyncio.run(write_results(path, results))
    except ArtifactStoreUnavailable as error:
        _stop(error.message, ExitCode.INFRASTRUCTURE)
    typer.echo(f"wrote {path}")


def _progress(result: CellResult) -> None:
    wrong = sum(1 for step in result.steps if step.outcome.wrong)
    name = " ".join(cell_key(result.cell)[1:])
    failure = result.run_failure
    where = f" at {failure.step_id}: {failure.reason}" if failure is not None else ""
    typer.echo(f"{name}: {result.run_status}{where}, {wrong} wrong")


def _summarize(results: BenchmarkResults) -> None:
    for section in results.sections:
        typer.echo(f"\n{section.title} ({len(section.cells):,} cells)")
        for summary in section.systems:
            metrics = summary.overall
            typer.echo(
                f"  {results.system(summary.system).label}: wrong-action steps "
                f"{fraction(metrics.wrong_action_rate).text}, false successes "
                f"{metrics.false_successes}; changed steps completed "
                f"{fraction(metrics.changed_step_completion).text}; correct abstentions "
                f"{fraction(metrics.correct_abstain).text}; unnecessary abstentions "
                f"{fraction(metrics.unnecessary_abstain).text}"
            )


def _stop(message: str, code: ExitCode) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)
