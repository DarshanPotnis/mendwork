"""``mendwork approve`` and ``mendwork reject``: a person's decision on a run's proposal (ADR 0011).

``approve`` records the approval in the audit log and the run's record, then resumes the run in a
new browser straight away. It reports as ``mendwork run`` does and exits with the resumed run's
code. ``reject`` records the rejection, which ends the run failed, and exits 0.

Both exit 2 when nothing could be recorded: an unknown run or proposal, a proposal already decided,
a run another process holds, or, for an approval, a run that cannot resume, a missing secret, or a
URL today's egress policy refuses. They exit 3 when the audit log cannot be trusted. A decision that
has begun to be recorded is always recorded whole; a first Ctrl+C then stops the resume where it is,
and a second aborts at once.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Annotated, TextIO

import typer

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import ChromiumLauncher
from mendwork.adapters.events_jsonl.sink import JsonLinesEventSink
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.system.resolver import SystemHostResolver
from mendwork.apps.cli.approval_output import outcome_line, rejected_lines
from mendwork.apps.cli.approval_wiring import build_desk, build_resumer
from mendwork.apps.cli.commands import (
    ArtifactsOption,
    OutputMode,
    OutputOption,
    RunIdArgument,
    invalid,
    process_scrubber,
    read_record,
    report_egress,
    report_error,
    report_inputs,
    report_nothing_recorded,
    report_run,
    report_secrets,
    result_line,
    run_id_or_exit,
    settings_or_exit,
)
from mendwork.apps.cli.exit_codes import ExitCode, exit_code_for
from mendwork.apps.cli.human_output import HumanProgress
from mendwork.apps.cli.interrupts import RunInterrupts, abort_run
from mendwork.apps.cli.wiring import (
    USAGE_DIRECTORY,
    egress_enforcement,
    launch_options,
    model_client,
    model_rung,
    session_options,
)
from mendwork.engine.domain.approvals import REASON_MAX_LENGTH
from mendwork.engine.domain.base import check_single_line
from mendwork.engine.domain.runs import Run, RunId
from mendwork.engine.errors import (
    EgressBlocked,
    InfrastructureError,
    ProposalNotPending,
    RunBusy,
    RunInputError,
    RunNotResumable,
    SecretUnavailable,
    UnknownRun,
)
from mendwork.engine.ports.events import EventSink
from mendwork.engine.safety.approvals import find_proposal
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings

ProposalArgument = Annotated[
    str,
    typer.Argument(metavar="PROPOSAL_ID", help="The proposal's id, as `mendwork show` lists it."),
]
NOTHING_RECORDED = "interrupted before the decision was recorded; nothing changed"


def approve(
    ctx: typer.Context,
    run_id: RunIdArgument,
    proposal_id: ProposalArgument,
    headed: Annotated[
        bool, typer.Option("--headed", help="Show the browser window while the run resumes.")
    ] = False,
    artifacts_dir: ArtifactsOption = None,
    output: OutputOption = OutputMode.HUMAN,
) -> None:
    """Approve a proposal and resume its run, acting only on the approved element."""
    stdout, stderr = sys.stdout, sys.stderr
    settings = settings_or_exit(output, stdout, stderr)
    run = run_id_or_exit(run_id, output, stdout, stderr)
    root = artifacts_dir or settings.artifacts_dir
    code = asyncio.run(
        _approve(
            run,
            proposal_id,
            settings,
            headed=headed,
            artifacts=LocalArtifactStore(root),
            usage_directory=root / USAGE_DIRECTORY,
            output=output,
            stdout=stdout,
            stderr=stderr,
            scrubber=process_scrubber(ctx),
        )
    )
    raise typer.Exit(code=code)


def reject(
    ctx: typer.Context,
    run_id: RunIdArgument,
    proposal_id: ProposalArgument,
    reason: Annotated[
        str | None,
        typer.Option(
            "--reason",
            metavar="TEXT",
            help=f"Why, in one line of at most {REASON_MAX_LENGTH} characters; kept in the "
            "audit log with secrets removed.",
        ),
    ] = None,
    artifacts_dir: ArtifactsOption = None,
    output: OutputOption = OutputMode.HUMAN,
) -> None:
    """Reject a proposal: its run ends failed, and nothing is acted on."""
    stdout, stderr = sys.stdout, sys.stderr
    settings = settings_or_exit(output, stdout, stderr)
    run = run_id_or_exit(run_id, output, stdout, stderr)
    problem = reason_problem(reason)
    if problem is not None:
        invalid(output, stdout, stderr, "InvalidReason", f"--reason {problem}")
    artifacts = LocalArtifactStore(artifacts_dir or settings.artifacts_dir)
    code = asyncio.run(
        _reject(
            run,
            proposal_id,
            reason,
            artifacts=artifacts,
            output=output,
            stdout=stdout,
            stderr=stderr,
            scrubber=process_scrubber(ctx),
        )
    )
    raise typer.Exit(code=code)


def reason_problem(reason: str | None) -> str | None:
    """Why a rejection reason cannot be recorded, or None when it can."""
    if reason is None:
        return None
    if len(reason) > REASON_MAX_LENGTH:
        return f"must be at most {REASON_MAX_LENGTH} characters"
    try:
        check_single_line(reason)
    except ValueError as error:
        return str(error)
    return None


async def _approve(
    run_id: RunId,
    proposal_id: str,
    settings: Settings,
    *,
    headed: bool,
    artifacts: LocalArtifactStore,
    usage_directory: Path,
    output: OutputMode,
    stdout: TextIO,
    stderr: TextIO,
    scrubber: SecretScrubber,
) -> int:
    sink: EventSink = (
        JsonLinesEventSink(stdout)
        if output is OutputMode.JSON
        else HumanProgress(stdout, artifacts.runs_root)
    )
    clock = SystemClock()
    interrupts = RunInterrupts(
        abort=lambda: abort_run(
            artifacts, run_id, clock=clock, stderr=stderr, exit_process=os._exit
        )
    )
    resolver = SystemHostResolver()
    launcher = ChromiumLauncher(
        launch_options(settings, headed=headed, slow_mo_ms=None),
        session_options(settings),
        egress_enforcement(settings, resolver),
    )
    desk = build_desk(artifacts=artifacts, environ=os.environ, scrubber=scrubber)
    try:
        async with interrupts.connected(), launcher, model_client(settings) as client:
            model = (
                model_rung(settings, client=client, ledger_directory=usage_directory)
                if client is not None
                else None
            )
            resumer = build_resumer(
                settings,
                launcher=launcher,
                artifacts=artifacts,
                events=sink,
                environ=os.environ,
                egress=settings.egress_policy(),
                resolver=resolver,
                scrubber=scrubber,
                model=model,
            )
            task = asyncio.ensure_future(desk.approve(run_id, proposal_id, resumer))
            interrupts.watch(task.cancel)
            try:
                finished = await task
            except asyncio.CancelledError:
                if not interrupts.interrupted:
                    raise
                return _interrupted(artifacts, run_id, proposal_id, output, stdout, stderr)
    except (RunBusy, UnknownRun, ProposalNotPending, RunNotResumable) as error:
        report_error(error, ExitCode.INVALID, output, stdout, stderr)
        return ExitCode.INVALID
    except RunInputError as error:
        report_inputs(error, f"run {run_id}", output, stdout, stderr)
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
    report_run(finished, code, artifacts.runs_root, output, stdout)
    line = outcome_line(finished, proposal_id)
    if output is OutputMode.HUMAN and line is not None:
        stdout.write(line + "\n")
    return code


async def _reject(
    run_id: RunId,
    proposal_id: str,
    reason: str | None,
    *,
    artifacts: LocalArtifactStore,
    output: OutputMode,
    stdout: TextIO,
    stderr: TextIO,
    scrubber: SecretScrubber,
) -> int:
    clock = SystemClock()
    interrupts = RunInterrupts(
        abort=lambda: abort_run(
            artifacts, run_id, clock=clock, stderr=stderr, exit_process=os._exit
        )
    )
    desk = build_desk(artifacts=artifacts, environ=os.environ, scrubber=scrubber)
    try:
        async with interrupts.connected():
            task = asyncio.ensure_future(desk.reject(run_id, proposal_id, reason))
            interrupts.watch(task.cancel)
            try:
                finished: Run | None = await task
            except asyncio.CancelledError:
                if not interrupts.interrupted:
                    raise
                finished = _decided(read_record(artifacts, run_id), proposal_id)
    except (RunBusy, UnknownRun, ProposalNotPending) as error:
        report_error(error, ExitCode.INVALID, output, stdout, stderr)
        return ExitCode.INVALID
    except InfrastructureError as error:
        report_error(error, ExitCode.INFRASTRUCTURE, output, stdout, stderr)
        return ExitCode.INFRASTRUCTURE
    if finished is None:
        return report_nothing_recorded(NOTHING_RECORDED, output, stdout, stderr)
    if output is OutputMode.JSON:
        result_line(stdout, ExitCode.SUCCEEDED, run=json.loads(finished.model_dump_json()))
    else:
        stdout.write("\n".join(rejected_lines(finished, proposal_id)) + "\n")
    return ExitCode.SUCCEEDED


def _decided(record: Run | None, proposal_id: str) -> Run | None:
    """The record, if the decision on the proposal reached it."""
    item = find_proposal(record, proposal_id) if record is not None else None
    return record if item is not None and not item.pending else None


def _interrupted(
    artifacts: LocalArtifactStore,
    run_id: RunId,
    proposal_id: str,
    output: OutputMode,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    record = _decided(read_record(artifacts, run_id), proposal_id)
    if record is None:
        return report_nothing_recorded(NOTHING_RECORDED, output, stdout, stderr)
    code = exit_code_for(record)
    report_run(record, code, artifacts.runs_root, output, stdout)
    return code
