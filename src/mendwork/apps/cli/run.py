"""``mendwork run``: replay a workflow in a real browser and report what happened.

Output channels: progress and the summary (or JSON lines in ``--output json``) go to
stdout; logs always go to stderr. Exit codes are in ``exit_codes``. A first Ctrl+C (or SIGTERM)
stops the run where it is and reports its record; a second aborts at once (``interrupts``).
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Annotated, NoReturn, TextIO

import typer

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import ChromiumLauncher
from mendwork.adapters.events_jsonl.sink import JsonLinesEventSink
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.system.resolver import SystemHostResolver
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.arguments import parse_input_arguments
from mendwork.apps.cli.commands import (
    ArtifactsOption,
    OutputMode,
    OutputOption,
    error_json,
    process_scrubber,
    report_egress,
    report_error,
    report_inputs,
    report_interrupted,
    report_run,
    report_secrets,
    result_line,
    settings_or_exit,
)
from mendwork.apps.cli.exit_codes import ExitCode, exit_code_for
from mendwork.apps.cli.human_output import HumanProgress
from mendwork.apps.cli.interrupts import RunInterrupts, RunWitness, abort_run
from mendwork.apps.cli.validate import format_problems, read_limited
from mendwork.apps.cli.wiring import (
    USAGE_DIRECTORY,
    build_replayer,
    egress_enforcement,
    launch_options,
    model_client,
    model_rung,
    session_options,
)
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import (
    EgressBlocked,
    InfrastructureError,
    RunInputError,
    SecretUnavailable,
    WorkflowValidationError,
)
from mendwork.engine.ports.events import EventSink
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings

__all__ = ["OutputMode", "run"]


def run(
    ctx: typer.Context,
    workflow: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="Workflow YAML file."),
    ],
    inputs: Annotated[
        list[str] | None,
        typer.Option(
            "--input", "-i", metavar="NAME=VALUE", help="A run input; repeat for each input."
        ),
    ] = None,
    headed: Annotated[
        bool, typer.Option("--headed", help="Show the browser window while the run replays.")
    ] = False,
    slow_mo: Annotated[
        int | None,
        typer.Option(
            "--slow-mo",
            min=0,
            max=10_000,
            metavar="MS",
            help="Pause after every browser operation, so recordings show each action.",
        ),
    ] = None,
    artifacts_dir: ArtifactsOption = None,
    output: OutputOption = OutputMode.HUMAN,
) -> None:
    """Replay a workflow, verifying every step; stop safely with evidence when unsure."""
    stdout, stderr = sys.stdout, sys.stderr
    settings = settings_or_exit(output, stdout, stderr)
    version = _load(workflow, settings, output, stdout, stderr)
    try:
        supplied = parse_input_arguments(inputs or [])
    except RunInputError as error:
        _invalid_inputs(error, str(workflow), output, stdout, stderr)
    code = asyncio.run(
        _replay(
            version,
            supplied,
            settings,
            headed=headed,
            slow_mo_ms=slow_mo,
            artifacts=LocalArtifactStore(artifacts_dir or settings.artifacts_dir),
            usage_directory=(artifacts_dir or settings.artifacts_dir) / USAGE_DIRECTORY,
            output=output,
            source=str(workflow),
            stdout=stdout,
            stderr=stderr,
            scrubber=process_scrubber(ctx),
        )
    )
    raise typer.Exit(code=code)


async def _replay(
    version: WorkflowVersion,
    supplied: dict[str, str],
    settings: Settings,
    *,
    headed: bool,
    slow_mo_ms: int | None,
    artifacts: LocalArtifactStore,
    usage_directory: Path,
    output: OutputMode,
    source: str,
    stdout: TextIO,
    stderr: TextIO,
    scrubber: SecretScrubber,
) -> int:
    runs_directory = artifacts.runs_root
    sink: EventSink = (
        JsonLinesEventSink(stdout)
        if output is OutputMode.JSON
        else HumanProgress(stdout, runs_directory)
    )
    witness = RunWitness(sink)
    clock = SystemClock()
    interrupts = RunInterrupts(
        abort=lambda: abort_run(
            artifacts, witness.run_id, clock=clock, stderr=stderr, exit_process=os._exit
        )
    )
    resolver = SystemHostResolver()
    launcher = ChromiumLauncher(
        launch_options(settings, headed=headed, slow_mo_ms=slow_mo_ms),
        session_options(settings),
        egress_enforcement(settings, resolver),
    )
    try:
        async with interrupts.connected(), launcher, model_client(settings) as client:
            model = (
                model_rung(settings, client=client, ledger_directory=usage_directory)
                if client is not None
                else None
            )
            replayer = build_replayer(
                settings,
                launcher=launcher,
                artifacts=artifacts,
                events=witness,
                environ=os.environ,
                egress=settings.egress_policy(),
                resolver=resolver,
                scrubber=scrubber,
                model=model,
            )
            task = asyncio.ensure_future(replayer.run(version, supplied))
            interrupts.watch(task.cancel)
            try:
                finished = await task
            except asyncio.CancelledError:
                if not interrupts.interrupted:
                    raise
                return report_interrupted(artifacts, witness.run_id, output, stdout, stderr)
    except RunInputError as error:
        report_inputs(error, source, output, stdout, stderr)
        return ExitCode.INVALID
    except SecretUnavailable as error:
        report_secrets(error, output, stdout, stderr)
        return ExitCode.INVALID
    except EgressBlocked as error:
        report_egress(error, output, stdout, stderr)
        return ExitCode.INVALID
    except InfrastructureError as error:
        report_error(error, ExitCode.INFRASTRUCTURE, output, stdout, stderr)
        return ExitCode.INFRASTRUCTURE
    code = exit_code_for(finished)
    report_run(finished, code, runs_directory, output, stdout)
    return code


def _load(
    workflow: Path, settings: Settings, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> WorkflowVersion:
    codec = WorkflowYamlCodec(max_bytes=settings.workflow_max_bytes)
    source = str(workflow)
    content = asyncio.run(read_limited(workflow, codec.max_bytes))
    try:
        return codec.decode(content, source=source)
    except WorkflowValidationError as error:
        stderr.write(format_problems(source, error) + "\n")
        if output is OutputMode.JSON:
            result_line(stdout, ExitCode.INVALID, error=error_json(error))
        raise typer.Exit(code=ExitCode.INVALID) from None


def _invalid_inputs(
    error: RunInputError, source: str, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> NoReturn:
    report_inputs(error, source, output, stdout, stderr)
    raise typer.Exit(code=ExitCode.INVALID)
