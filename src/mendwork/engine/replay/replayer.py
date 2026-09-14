"""The replayer: runs a workflow version, healing where Rung 0 cannot, and keeps its record.

Preflight comes first and creates nothing: inputs are bound and secrets checked before a
run id exists, so an invalid run leaves no artifacts behind. From then on, every outcome
(success, a failed step, a broken browser, the run's time limit) ends with a run record
and a ``run_finished`` event.
"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Final

import structlog

from mendwork.engine.domain.model_evidence import ModelUsageTotals
from mendwork.engine.domain.runs import (
    STOPPING_STATUSES,
    ErrorCategory,
    ErrorReport,
    Run,
    RunStatus,
    StepResult,
    StepStatus,
)
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import InfrastructureError, RunTimedOut, SecretUnavailable
from mendwork.engine.healing.model_rung import ModelRung
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.ports.browser import BrowserLauncher
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.events import EventSink
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.run_ids import RunIdGenerator
from mendwork.engine.ports.secrets import SecretResolver
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.artifact_names import RUN_RECORD
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.inputs import bind_inputs
from mendwork.engine.replay.reports import error_report
from mendwork.engine.replay.step_runner import StepRunner
from mendwork.engine.replay.values import ValueResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber

_RUN_STATUS_FOR_STOP: Final = {
    StepStatus.FAILED: RunStatus.FAILED,
    StepStatus.AWAITING_APPROVAL: RunStatus.AWAITING_APPROVAL,
    StepStatus.NEEDS_REVIEW: RunStatus.NEEDS_REVIEW,
}


class Replayer:
    """Replays workflow versions step by step, acting only on verified targets."""

    def __init__(
        self,
        *,
        launcher: BrowserLauncher,
        artifacts: ArtifactStore,
        events: EventSink,
        secrets: SecretResolver,
        clock: Clock,
        timer: Timer,
        randomness: RandomSource,
        run_ids: RunIdGenerator,
        config: ReplayConfig,
        model: ModelRung | None = None,
    ) -> None:
        self._launcher = launcher
        self._artifacts = artifacts
        self._events = events
        self._secrets = secrets
        self._clock = clock
        self._timer = timer
        self._randomness = randomness
        self._run_ids = run_ids
        self._config = config
        self._model = model

    async def run(self, workflow: WorkflowVersion, supplied_inputs: Mapping[str, str]) -> Run:
        """Replay a workflow version with the given inputs.

        Raises RunInputError or SecretUnavailable before the run starts. Once it has
        started, step failures are reported in the returned run, not raised. An error that
        is not Mendwork's own is recorded, then raised as InfrastructureError.
        """
        inputs = bind_inputs(workflow.inputs, supplied_inputs)
        missing = await self._secrets.missing(workflow.secrets)
        if missing:
            raise SecretUnavailable(
                f"the workflow needs secrets that are not available: {', '.join(missing)}",
                names=list(missing),
            )

        run_id = self._run_ids.new_run_id()
        scrubber = SecretScrubber()
        log = structlog.stdlib.get_logger("mendwork.replay").bind(
            run_id=run_id, workflow_id=workflow.workflow_id
        )
        started = self._timer.monotonic()
        run_deadline = Deadline.after(self._timer, self._config.run_timeout_ms)
        emitter = RunEmitter(self._events, self._clock, run_id)
        chooser = self._model.for_run(self._clock) if self._model is not None else None
        record = Run(
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            status=RunStatus.RUNNING,
            started_at=self._clock.now(),
            inputs=dict(inputs),
            secrets=workflow.secrets,
            steps=_not_run(workflow, ()),
        )
        await self._write(record)
        await emitter.run_started(workflow)
        log.info("run_started", step_count=len(workflow.steps))

        results: list[StepResult] = []
        error: ErrorReport | None = None
        unexpected: Exception | None = None
        try:
            async with self._launcher.session(run_id) as browser:
                runner = StepRunner(
                    browser=browser,
                    artifacts=self._artifacts,
                    emitter=emitter,
                    values=ValueResolver(inputs, self._secrets, scrubber),
                    scrubber=scrubber,
                    clock=self._clock,
                    timer=self._timer,
                    randomness=self._randomness,
                    config=self._config,
                    run_id=run_id,
                    run_deadline=run_deadline,
                    log=log,
                    chooser=chooser,
                )
                timeout = await self._run_steps(workflow, runner, run_deadline, results)
                if timeout is not None:
                    error = error_report(timeout, scrubber)
        except InfrastructureError as failure:
            error = error_report(failure, scrubber)
        except Exception as failure:  # noqa: BLE001 - recorded in the run, then re-raised below
            error = ErrorReport(
                type=type(failure).__name__,
                message=scrubber.scrub_text(str(failure)),
                category=ErrorCategory.INFRASTRUCTURE,
            )
            unexpected = failure

        usage = chooser.budget.totals if chooser is not None else ModelUsageTotals()
        finished = self._finish(record, workflow, results, error, scrubber, started, usage)
        await self._write(finished)
        await emitter.run_finished(finished)
        log.info("run_finished", status=finished.status.value, duration_ms=finished.duration_ms)
        if unexpected is not None:
            raise InfrastructureError(
                "an unexpected error stopped the run",
                run_id=run_id,
                error_type=type(unexpected).__name__,
            ) from unexpected
        return finished

    async def _run_steps(
        self,
        workflow: WorkflowVersion,
        runner: StepRunner,
        run_deadline: Deadline,
        results: list[StepResult],
    ) -> RunTimedOut | None:
        # The deadline bounds every wait; this scope is the backstop for a browser call
        # that does not return at all.
        scope = asyncio.timeout(self._config.run_timeout_ms / 1000)
        try:
            async with scope:
                for index, step in enumerate(workflow.steps):
                    if run_deadline.expired:
                        return RunTimedOut(
                            f"the run exceeded its time limit of {self._config.run_timeout_ms} ms "
                            f"before step {step.id}",
                            reason="run_timeout",
                            run_timeout_ms=self._config.run_timeout_ms,
                        )
                    result = await runner.run(index, step)
                    results.append(result)
                    if result.status in STOPPING_STATUSES:
                        return None
        except TimeoutError:
            if not scope.expired():
                raise
            interrupted = await runner.interrupted_by_run_timeout()
            if interrupted is not None:
                results.append(interrupted)
        return None

    def _finish(
        self,
        record: Run,
        workflow: WorkflowVersion,
        results: list[StepResult],
        error: ErrorReport | None,
        scrubber: SecretScrubber,
        started: float,
        model_usage: ModelUsageTotals,
    ) -> Run:
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
        return Run(
            run_id=record.run_id,
            workflow_id=record.workflow_id,
            workflow_version=record.workflow_version,
            status=status,
            started_at=record.started_at,
            finished_at=self._clock.now(),
            duration_ms=round(max(0.0, self._timer.monotonic() - started) * 1000),
            inputs={name: scrubber.scrub_text(value) for name, value in record.inputs.items()},
            secrets=record.secrets,
            steps=_not_run(workflow, results),
            error=final_error,
            model_usage=model_usage,
        )

    async def _write(self, run: Run) -> None:
        await self._artifacts.write(
            run.run_id, RUN_RECORD, (run.model_dump_json(indent=2) + "\n").encode("utf-8")
        )


def _not_run(workflow: WorkflowVersion, results: Sequence[StepResult]) -> tuple[StepResult, ...]:
    """The results so far, followed by a not_run result for every step after them."""
    ran = list(results)
    rest = [
        StepResult(step_id=step.id, index=index, action=step.action, status=StepStatus.NOT_RUN)
        for index, step in enumerate(workflow.steps)
        if index >= len(ran)
    ]
    return tuple(ran + rest)
