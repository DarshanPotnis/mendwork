"""One execution of a run: a browser session, its steps, and the record it leaves (ADR 0011).

A run executes once when it starts, and once more each time an approval resumes it. Either way an
execution opens one isolated browser session held to the egress policy, runs steps in order until
one stops the run or all have run, and always ends with a final record and a ``run_finished``
event: after a step failure, a broken browser, the run's time limit, or an interrupt.

The journal keeps the record on disk current while steps run. An interrupt (the task is cancelled)
stops the step in progress where it is: its result is recorded without touching the browser again,
the run ends ``cancelled`` or ``needs_review`` (``safety.interruption``), and the cancellation is
re-raised to the caller once the record is written.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import structlog

from mendwork.engine.domain.approvals import VerifiedHealRecord
from mendwork.engine.domain.heals import HealProposal
from mendwork.engine.domain.identifiers import InputName
from mendwork.engine.domain.model_evidence import ModelUsageTotals
from mendwork.engine.domain.runs import (
    STOPPING_STATUSES,
    ErrorCategory,
    ErrorReport,
    Run,
    RunId,
    RunStatus,
    StepResult,
    StepStatus,
)
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import InfrastructureError, RunTimedOut
from mendwork.engine.healing.model_rung import ModelChooser
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.ports.browser import BrowserLauncher
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.secrets import SecretResolver
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.approval_records import paused, settle
from mendwork.engine.replay.artifact_names import FIRST_SEGMENT
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.journal import RunJournal, steps_so_far
from mendwork.engine.replay.navigation_guard import NavigationGuard
from mendwork.engine.replay.reports import error_report
from mendwork.engine.replay.step_runner import StepRunner
from mendwork.engine.replay.values import ValueResolver
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.interruption import (
    Interruption,
    cancellation_report,
    interrupted_status,
)
from mendwork.engine.safety.secret_scrub import SecretScrubber

_RUN_STATUS_FOR_STOP: Final = {
    StepStatus.FAILED: RunStatus.FAILED,
    StepStatus.CANCELLED: RunStatus.CANCELLED,
    StepStatus.AWAITING_APPROVAL: RunStatus.AWAITING_APPROVAL,
    StepStatus.NEEDS_REVIEW: RunStatus.NEEDS_REVIEW,
}

OpeningSteps = Callable[[StepRunner, list[StepResult]], Awaitable[bool]]
"""Work done inside the session before the ordinary step loop, such as a resume's approved step.

It appends the results it produces and returns whether the run continues."""


@dataclass(frozen=True, slots=True)
class ExecutionPorts:
    """The ports and settings every execution of a run uses."""

    launcher: BrowserLauncher
    artifacts: ArtifactStore
    secrets: SecretResolver
    clock: Clock
    timer: Timer
    randomness: RandomSource
    config: ReplayConfig
    egress: EgressPolicy


@dataclass(frozen=True, slots=True)
class Execution:
    """One execution of one run: what it runs, and what earlier executions left."""

    run_id: RunId
    workflow: WorkflowVersion
    inputs: Mapping[InputName, str]
    guard: NavigationGuard
    scrubber: SecretScrubber
    journal: RunJournal
    emitter: RunEmitter
    log: structlog.stdlib.BoundLogger
    chooser: ModelChooser | None = None
    prior: tuple[StepResult, ...] = ()
    """Results from earlier executions; this one continues after them."""
    prior_usage: ModelUsageTotals = field(default_factory=ModelUsageTotals)
    prior_duration_ms: int = 0
    segment: int = FIRST_SEGMENT
    approved: HealProposal | None = None
    """For a resume, the proposal a person approved; its outcome is settled in the final record."""
    verified: tuple[VerifiedHealRecord, ...] = ()
    """Verified heals earlier executions proved, found again while earlier steps are replayed."""
    proposals_made: int = 0
    downloads: frozenset[str] = frozenset()
    """Downloads earlier executions kept, so none is replaced."""


async def execute(
    ports: ExecutionPorts, execution: Execution, *, opening: OpeningSteps | None = None
) -> Run:
    """Run the execution's steps and return the final record.

    An interrupt is recorded (the run ends cancelled or needs review) and then re-raised. An error
    that is not Mendwork's own is recorded, then raised as InfrastructureError.
    """
    started = ports.timer.monotonic()
    run_deadline = Deadline.after(ports.timer, ports.config.run_timeout_ms)
    results = list(execution.prior)
    error: ErrorReport | None = None
    unexpected: Exception | None = None
    interrupt: asyncio.CancelledError | None = None
    runner: StepRunner | None = None
    try:
        async with ports.launcher.session(execution.run_id, ports.egress) as browser:
            runner = StepRunner(
                browser=browser,
                artifacts=ports.artifacts,
                emitter=execution.emitter,
                guard=execution.guard,
                on_irreversible=execution.journal.irreversible_dispatch,
                values=ValueResolver(execution.inputs, ports.secrets, execution.scrubber),
                scrubber=execution.scrubber,
                clock=ports.clock,
                timer=ports.timer,
                randomness=ports.randomness,
                config=ports.config,
                run_id=execution.run_id,
                run_deadline=run_deadline,
                log=execution.log,
                chooser=execution.chooser,
                segment=execution.segment,
                downloads=execution.downloads,
            )
            runner.seed(execution.verified, proposals_made=execution.proposals_made)
            timeout = await _run_steps(ports, execution, runner, run_deadline, results, opening)
            if timeout is not None:
                error = error_report(timeout, execution.scrubber)
    except asyncio.CancelledError as cancelled:
        interrupt = cancelled
    except InfrastructureError as failure:
        error = error_report(failure, execution.scrubber)
    except Exception as failure:  # noqa: BLE001 - recorded in the run, then re-raised below
        error = ErrorReport(
            type=type(failure).__name__,
            message=execution.scrubber.scrub_text(str(failure)),
            category=ErrorCategory.INFRASTRUCTURE,
        )
        unexpected = failure

    final_error: ErrorReport | None
    if interrupt is not None and not _ended(execution.workflow, results):
        dispatched = execution.journal.record.irreversible_dispatched
        report = cancellation_report(dispatched, Interruption.INTERRUPT)
        if runner is not None:
            pending = await runner.interrupted_by_cancellation(report)
            if pending is not None:
                results.append(pending)
        status, final_error = interrupted_status(dispatched), report
    else:
        status, final_error = _outcome(execution.workflow, results, error)
    finished = _final(ports, execution, runner, results, status, final_error, started)
    await execution.journal.write(finished)
    await execution.emitter.run_finished(finished)
    execution.log.info(
        "run_finished", status=finished.status.value, duration_ms=finished.duration_ms
    )
    if interrupt is not None:
        raise interrupt
    if unexpected is not None:
        raise InfrastructureError(
            "an unexpected error stopped the run",
            run_id=execution.run_id,
            error_type=type(unexpected).__name__,
        ) from unexpected
    return finished


async def _run_steps(
    ports: ExecutionPorts,
    execution: Execution,
    runner: StepRunner,
    run_deadline: Deadline,
    results: list[StepResult],
    opening: OpeningSteps | None,
) -> RunTimedOut | None:
    # The deadline bounds every wait; this scope is the backstop for a browser call that does
    # not return at all.
    run_timeout_ms = ports.config.run_timeout_ms
    scope = asyncio.timeout(run_timeout_ms / 1000)
    steps = execution.workflow.steps
    try:
        async with scope:
            if opening is not None:
                proceed = await opening(runner, results)
                await execution.journal.results(results)
                if not proceed:
                    return None
            for index in range(len(results), len(steps)):
                step = steps[index]
                if run_deadline.expired:
                    return RunTimedOut(
                        f"the run exceeded its time limit of {run_timeout_ms} ms before step "
                        f"{step.id}",
                        reason="run_timeout",
                        run_timeout_ms=run_timeout_ms,
                    )
                result = await runner.run(index, step)
                results.append(result)
                await execution.journal.results(results)
                if result.status in STOPPING_STATUSES:
                    return None
    except TimeoutError:
        if not scope.expired():
            raise
        interrupted = await runner.interrupted_by_run_timeout()
        if interrupted is not None:
            results.append(interrupted)
    return None


def _ended(workflow: WorkflowVersion, results: Sequence[StepResult]) -> bool:
    """Whether the steps had already reached their end when an interrupt arrived."""
    stopped = any(result.status in STOPPING_STATUSES for result in results)
    return stopped or len(results) == len(workflow.steps)


def _outcome(
    workflow: WorkflowVersion, results: Sequence[StepResult], error: ErrorReport | None
) -> tuple[RunStatus, ErrorReport | None]:
    stopped = next((result for result in results if result.status in STOPPING_STATUSES), None)
    final_error = error or (stopped.error if stopped is not None else None)
    complete = len(results) == len(workflow.steps)
    status = RunStatus.SUCCEEDED if final_error is None and complete else RunStatus.FAILED
    if error is None and stopped is not None:
        status = _RUN_STATUS_FOR_STOP[stopped.status]
    if status is RunStatus.FAILED and final_error is None:
        final_error = ErrorReport(
            type="RunIncomplete",
            message="the run stopped before every step ran",
            category=ErrorCategory.INFRASTRUCTURE,
        )
    return status, final_error


def _final(
    ports: ExecutionPorts,
    execution: Execution,
    runner: StepRunner | None,
    results: Sequence[StepResult],
    status: RunStatus,
    error: ErrorReport | None,
    started: float,
) -> Run:
    """The final record: its steps, status, usage, and segment, and where its approvals stand.

    A run that paused for approval records its proposal as pending, and whether its inputs can be
    resumed: an input that held a secret's value is kept only in its redacted form.
    """
    record = execution.journal.record
    now = ports.clock.now()
    elapsed_ms = round(max(0.0, ports.timer.monotonic() - started) * 1000)
    segments = list(record.segments)
    if segments:
        segments[-1] = segments[-1].model_copy(
            update={"finished_at": now, "duration_ms": elapsed_ms}
        )
    usage = execution.prior_usage
    if execution.chooser is not None:
        usage = usage.combined(execution.chooser.budget.totals)
    finished = record.model_copy(
        update={
            "status": status,
            "finished_at": now,
            "duration_ms": execution.prior_duration_ms + elapsed_ms,
            "steps": steps_so_far(execution.workflow, results),
            "error": error,
            "model_usage": usage,
            "segments": tuple(segments),
            "last_event_sequence": execution.emitter.sequence + 1,
        }
    )
    if execution.approved is not None:
        finished = settle(finished, execution.approved)
    scrubber = execution.scrubber
    return paused(
        finished,
        inputs_recoverable=all(
            scrubber.scrub_text(value) == value for value in execution.inputs.values()
        ),
        verified=runner.verified_heal_records() if runner is not None else (),
    )
