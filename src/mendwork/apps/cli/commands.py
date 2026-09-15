"""What the commands that work with runs share: their options, settings, and how they report.

Used by ``mendwork run``, ``approve``, ``reject``, and ``show``. Progress and summaries (or one JSON
result line in ``--output json``) go to stdout; problems and logs go to stderr. Exit codes are in
``exit_codes``.
"""

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn, TextIO

import typer
from pydantic import ValidationError

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.secrets_env.naming import secret_variable_name
from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.apps.cli.heal_output import egress_next_step
from mendwork.apps.cli.human_output import render_summary
from mendwork.apps.cli.validate import format_issue
from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.domain.runs import Run, RunId, parse_run_id
from mendwork.engine.errors import (
    EgressBlocked,
    InfrastructureError,
    MendworkError,
    RunInputError,
    SecretUnavailable,
)
from mendwork.engine.replay.artifact_names import RUN_RECORD
from mendwork.engine.replay.reports import to_json_value
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings


class OutputMode(StrEnum):
    """How a command reports on stdout."""

    HUMAN = "human"
    JSON = "json"


RunIdArgument = Annotated[
    str, typer.Argument(metavar="RUN_ID", help="The run's id, as `mendwork run` printed it.")
]
ArtifactsOption = Annotated[
    Path | None,
    typer.Option("--artifacts-dir", file_okay=False, help="Where run artifacts are written."),
]
OutputOption = Annotated[
    OutputMode,
    typer.Option("--output", case_sensitive=False, help="human (default) or json lines."),
]


def process_scrubber(ctx: typer.Context) -> SecretScrubber:
    """The scrubber the entry point gave this process's log pipeline (``main``)."""
    scrubber = ctx.find_root().obj
    if not isinstance(scrubber, SecretScrubber):
        raise InfrastructureError("the command was started without the process's secret scrubber")
    return scrubber


def settings_or_exit(output: OutputMode, stdout: TextIO, stderr: TextIO) -> Settings:
    """The process's Settings, or exit 2 naming what is invalid."""
    try:
        return Settings()
    except ValidationError as error:
        invalid(output, stdout, stderr, "InvalidSettings", f"invalid configuration: {error}")


def run_id_or_exit(value: str, output: OutputMode, stdout: TextIO, stderr: TextIO) -> RunId:
    """A run id from the command line, or exit 2."""
    try:
        return parse_run_id(value)
    except ValueError as error:
        invalid(output, stdout, stderr, "InvalidRunId", f"{value!r} is not a run id: {error}")


def read_record(artifacts: LocalArtifactStore, run_id: RunId | None) -> Run | None:
    """The run's record on disk, or None when there is none yet."""
    data = None if run_id is None else artifacts.read_now(run_id, RUN_RECORD)
    return None if data is None else Run.model_validate_json(data)


def report_run(
    run: Run,
    code: int,
    runs_directory: Path,
    output: OutputMode,
    stdout: TextIO,
    *,
    report: Path | None = None,
) -> None:
    """A finished run's summary and where its report is, or its JSON result line."""
    if output is OutputMode.JSON:
        fields: dict[str, object] = {"run": json.loads(run.model_dump_json())}
        if report is not None:
            fields["report"] = str(report)
        result_line(stdout, code, **fields)
    else:
        stdout.write(render_summary(run, runs_directory) + "\n")
        if report is not None:
            stdout.write(report_line(report) + "\n")
    stdout.flush()


def report_line(report: Path) -> str:
    """The line that points a person at a run's HTML report."""
    return f"Report: {report.resolve().as_uri()}"


def report_nothing_recorded(
    message: str, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> int:
    """An interrupt that came before anything was recorded: exit 130."""
    stderr.write(message[0].upper() + message[1:] + ".\n")
    if output is OutputMode.JSON:
        result_line(stdout, ExitCode.CANCELLED, error={"type": "RunCancelled", "message": message})
    return ExitCode.CANCELLED


def report_inputs(
    error: RunInputError, source: str, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> None:
    """Every invalid run input."""
    lines = [f"{source}: invalid run inputs"]
    lines.extend(format_issue(source, issue) for issue in error.issues)
    stderr.write("\n".join(lines) + "\n")
    if output is OutputMode.JSON:
        result_line(stdout, ExitCode.INVALID, error=error_json(error))


def report_secrets(
    error: SecretUnavailable, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> None:
    """Missing secrets, with the variable that supplies each."""
    names = error.context.get("names")
    listed = [str(name) for name in names] if isinstance(names, list) else []
    lines = [error.message]
    lines.extend(f"  set {secret_variable_name(SecretName(name))}" for name in listed)
    stderr.write("\n".join(lines) + "\n")
    if output is OutputMode.JSON:
        result_line(stdout, ExitCode.INVALID, error=error_json(error))


def report_egress(error: EgressBlocked, output: OutputMode, stdout: TextIO, stderr: TextIO) -> None:
    """A URL the egress policy refused before anything ran, and what to do about it."""
    context = to_json_value(dict(error.context))
    guidance = egress_next_step(context if isinstance(context, dict) else {})
    stderr.write(f"{error.message}; nothing ran\n{guidance}\n")
    if output is OutputMode.JSON:
        result_line(stdout, ExitCode.INVALID, error=error_json(error))


def report_error(
    error: MendworkError, code: ExitCode, output: OutputMode, stdout: TextIO, stderr: TextIO
) -> None:
    """Any other error, by type and message."""
    stderr.write(f"{type(error).__name__}: {error.message}\n")
    if output is OutputMode.JSON:
        result_line(stdout, code, error=error_json(error))


def invalid(
    output: OutputMode, stdout: TextIO, stderr: TextIO, error_type: str, message: str
) -> NoReturn:
    """Exit 2 with a message, before anything ran."""
    stderr.write(message + "\n")
    if output is OutputMode.JSON:
        result_line(stdout, ExitCode.INVALID, error={"type": error_type, "message": message})
    raise typer.Exit(code=ExitCode.INVALID)


def error_json(error: MendworkError) -> dict[str, object]:
    """An error as the JSON result line reports it."""
    report: dict[str, object] = {"type": type(error).__name__, "message": error.message}
    issues = getattr(error, "issues", ())
    if issues:
        report["issues"] = [{"path": issue.path, "message": issue.message} for issue in issues]
    return report


def result_line(stdout: TextIO, code: int, **fields: object) -> None:
    """The one JSON line a command ends with in ``--output json``."""
    stdout.write(json.dumps({"result_version": 1, "exit_code": code, **fields}) + "\n")
    stdout.flush()
