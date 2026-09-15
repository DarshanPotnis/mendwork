"""``mendwork show``: a run as it stands, with its proposals and the commands that decide them.

A run no process holds is shown as it was left, completed from its journal and the audit log, so a
run whose process stopped reads as stopped rather than still running. Nothing is written, and no
browser starts. Exits 0, 2 for a run with no record, or 3 when the audit log cannot be trusted.
"""

import asyncio
import json
import os
import sys
from typing import TextIO

import typer

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.apps.cli.approval_output import render_show
from mendwork.apps.cli.approval_wiring import build_desk
from mendwork.apps.cli.commands import (
    ArtifactsOption,
    OutputMode,
    OutputOption,
    RunIdArgument,
    process_scrubber,
    report_error,
    result_line,
    run_id_or_exit,
    settings_or_exit,
)
from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.apps.cli.run_reports import saved_workflow
from mendwork.engine.domain.runs import RunId
from mendwork.engine.errors import InfrastructureError, UnknownRun
from mendwork.engine.safety.secret_scrub import SecretScrubber


def show(
    ctx: typer.Context,
    run_id: RunIdArgument,
    artifacts_dir: ArtifactsOption = None,
    output: OutputOption = OutputMode.HUMAN,
) -> None:
    """Show a run as it stands: its steps, its proposals, and how to decide them."""
    stdout, stderr = sys.stdout, sys.stderr
    settings = settings_or_exit(output, stdout, stderr)
    run = run_id_or_exit(run_id, output, stdout, stderr)
    artifacts = LocalArtifactStore(artifacts_dir or settings.artifacts_dir)
    scrubber = process_scrubber(ctx)
    raise typer.Exit(code=asyncio.run(_show(run, artifacts, output, stdout, stderr, scrubber)))


async def _show(
    run_id: RunId,
    artifacts: LocalArtifactStore,
    output: OutputMode,
    stdout: TextIO,
    stderr: TextIO,
    scrubber: SecretScrubber,
) -> int:
    desk = build_desk(artifacts=artifacts, environ=os.environ, scrubber=scrubber)
    try:
        record = await desk.current(run_id)
    except UnknownRun as error:
        report_error(error, ExitCode.INVALID, output, stdout, stderr)
        return ExitCode.INVALID
    except InfrastructureError as error:
        report_error(error, ExitCode.INFRASTRUCTURE, output, stdout, stderr)
        return ExitCode.INFRASTRUCTURE
    if output is OutputMode.JSON:
        result_line(stdout, ExitCode.SUCCEEDED, run=json.loads(record.model_dump_json()))
    else:
        workflow = saved_workflow(artifacts, run_id)
        stdout.write(render_show(record, workflow, artifacts.runs_root) + "\n")
    return ExitCode.SUCCEEDED
