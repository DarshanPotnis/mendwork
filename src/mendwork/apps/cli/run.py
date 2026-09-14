"""``mendwork run``: replay a workflow in a real browser and report what happened.

Output channels: progress and the summary (or JSON lines in ``--output json``) go to
stdout; logs always go to stderr. Exit codes are in ``exit_codes``.
"""

import asyncio
import json
import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn, TextIO

import typer
from pydantic import ValidationError

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import ChromiumLauncher
from mendwork.adapters.events_jsonl.sink import JsonLinesEventSink
from mendwork.adapters.secrets_env.naming import secret_variable_name
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.arguments import parse_input_arguments
from mendwork.apps.cli.exit_codes import ExitCode, exit_code_for
from mendwork.apps.cli.human_output import HumanProgress, render_summary
from mendwork.apps.cli.validate import format_issue, format_problems, read_limited
from mendwork.apps.cli.wiring import (
    USAGE_DIRECTORY,
    build_replayer,
    launch_options,
    model_client,
    model_rung,
    session_options,
)
from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.domain.runs import Run
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import (
    InfrastructureError,
    MendworkError,
    RunInputError,
    SecretUnavailable,
    WorkflowValidationError,
)
from mendwork.engine.ports.events import EventSink
from mendwork.settings import Settings


class OutputMode(StrEnum):
    """How ``mendwork run`` reports on stdout."""

    HUMAN = "human"
    JSON = "json"


def run(
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
    artifacts_dir: Annotated[
        Path | None,
        typer.Option("--artifacts-dir", file_okay=False, help="Where run artifacts are written."),
    ] = None,
    output: Annotated[
        OutputMode,
        typer.Option("--output", case_sensitive=False, help="human (default) or json lines."),
    ] = OutputMode.HUMAN,
) -> None:
    """Replay a workflow, verifying every step; stop safely with evidence when unsure."""
    stdout, stderr = sys.stdout, sys.stderr
    try:
        settings = Settings()
    except ValidationError as error:
        _invalid(output, stdout, stderr, "InvalidSettings", f"invalid configuration: {error}")
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
) -> int:
    runs_directory = artifacts.runs_root
    events: EventSink = (
        JsonLinesEventSink(stdout)
        if output is OutputMode.JSON
        else HumanProgress(stdout, runs_directory)
    )
    launcher = ChromiumLauncher(
        launch_options(settings, headed=headed, slow_mo_ms=slow_mo_ms), session_options(settings)
    )
    try:
        async with launcher, model_client(settings) as client:
            model = (
                model_rung(settings, client=client, ledger_directory=usage_directory)
                if client is not None
                else None
            )
            replayer = build_replayer(
                settings,
                launcher=launcher,
                artifacts=artifacts,
                events=events,
                environ=os.environ,
                model=model,
            )
            finished = await replayer.run(version, supplied)
    except RunInputError as error:
        _report_inputs(error, source, output, stdout, stderr)
        return ExitCode.INVALID
    except SecretUnavailable as error:
        _report_secrets(error, output, stdout, stderr)
        return ExitCode.INVALID
    except InfrastructureError as error:
        _report_error(error, ExitCode.INFRASTRUCTURE, output, stdout, stderr)
        return ExitCode.INFRASTRUCTURE
    code = exit_code_for(finished)
    _report_run(finished, code, runs_directory, output, stdout)
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
            _result_line(stdout, ExitCode.INVALID, error=_error_json(error))
        raise typer.Exit(code=ExitCode.INVALID) from None


def _report_run(
    run: Run, code: int, runs_directory: Path, output: OutputMode, stdout: TextIO
) -> None:
    if output is OutputMode.JSON:
        _result_line(stdout, code, run=json.loads(run.model_dump_json()))
    else:
        stdout.write(render_summary(run, runs_directory) + "\n")
    stdout.flush()


def _invalid_inputs(
    error: RunInputError, source: str, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> NoReturn:
    _report_inputs(error, source, output, stdout, stderr)
    raise typer.Exit(code=ExitCode.INVALID)


def _report_inputs(
    error: RunInputError, source: str, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> None:
    lines = [f"{source}: invalid run inputs"]
    lines.extend(format_issue(source, issue) for issue in error.issues)
    stderr.write("\n".join(lines) + "\n")
    if output is OutputMode.JSON:
        _result_line(stdout, ExitCode.INVALID, error=_error_json(error))


def _report_secrets(
    error: SecretUnavailable, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> None:
    names = error.context.get("names")
    listed = [str(name) for name in names] if isinstance(names, list) else []
    lines = [error.message]
    lines.extend(f"  set {secret_variable_name(SecretName(name))}" for name in listed)
    stderr.write("\n".join(lines) + "\n")
    if output is OutputMode.JSON:
        _result_line(stdout, ExitCode.INVALID, error=_error_json(error))


def _report_error(
    error: MendworkError, code: ExitCode, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> None:
    stderr.write(f"{type(error).__name__}: {error.message}\n")
    if output is OutputMode.JSON:
        _result_line(stdout, code, error=_error_json(error))


def _invalid(
    output: OutputMode, stdout: TextIO, stderr: TextIO, error_type: str, message: str
) -> NoReturn:
    stderr.write(message + "\n")
    if output is OutputMode.JSON:
        _result_line(stdout, ExitCode.INVALID, error={"type": error_type, "message": message})
    raise typer.Exit(code=ExitCode.INVALID)


def _error_json(error: MendworkError) -> dict[str, object]:
    report: dict[str, object] = {"type": type(error).__name__, "message": error.message}
    issues = getattr(error, "issues", ())
    if issues:
        report["issues"] = [{"path": issue.path, "message": issue.message} for issue in issues]
    return report


def _result_line(stdout: TextIO, code: int, **fields: object) -> None:
    stdout.write(json.dumps({"result_version": 1, "exit_code": code, **fields}) + "\n")
    stdout.flush()
