"""Stamping and sending a run's events.

Every event gets the run id, the next number of a gap-free sequence starting at 1, and a
timestamp from the Clock port. Text inside events is scrubbed where it enters the run
(errors, identities, checkpoint details, URLs), so the emitter sends what it is given.
"""

from mendwork.engine.domain.enums import ValueKind
from mendwork.engine.domain.events import (
    ActionPerformedEvent,
    CheckpointFailedEvent,
    CheckpointPassedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFailedEvent,
    StepStartedEvent,
    StepSucceededEvent,
    TargetResolvedEvent,
)
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.runs import (
    CheckpointResult,
    NavigationReport,
    Run,
    RunId,
    StepResult,
    TargetEvidence,
)
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.events import EventSink


class RunEmitter:
    """Builds each event type from the run's own records and sends it to the sink."""

    def __init__(self, sink: EventSink, clock: Clock, run_id: RunId) -> None:
        self._sink = sink
        self._clock = clock
        self._run_id = run_id
        self._sequence = 0

    def _next(self) -> int:
        self._sequence += 1
        return self._sequence

    async def run_started(self, workflow: WorkflowVersion) -> None:
        """The run passed preflight."""
        await self._sink.emit(
            RunStartedEvent(
                run_id=self._run_id,
                sequence=self._next(),
                at=self._clock.now(),
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.version,
                step_count=len(workflow.steps),
                input_names=tuple(declaration.name for declaration in workflow.inputs),
                secret_names=workflow.secrets,
            )
        )

    async def step_started(self, index: int, step: Step) -> None:
        """A step began."""
        await self._sink.emit(
            StepStartedEvent(
                run_id=self._run_id,
                sequence=self._next(),
                at=self._clock.now(),
                step_id=step.id,
                index=index,
                action=step.action,
                risk=step.risk,
                intent=step.intent,
            )
        )

    async def target_resolved(self, index: int, step_id: StepId, evidence: TargetEvidence) -> None:
        """Rung 0 verified the target."""
        await self._sink.emit(
            TargetResolvedEvent(
                run_id=self._run_id,
                sequence=self._next(),
                at=self._clock.now(),
                step_id=step_id,
                index=index,
                evidence=evidence,
            )
        )

    async def action_performed(
        self,
        index: int,
        step: Step,
        value_kind: ValueKind | None = None,
        navigation: NavigationReport | None = None,
    ) -> None:
        """The action reached the page."""
        await self._sink.emit(
            ActionPerformedEvent(
                run_id=self._run_id,
                sequence=self._next(),
                at=self._clock.now(),
                step_id=step.id,
                index=index,
                action=step.action,
                value_kind=value_kind,
                navigation=navigation,
            )
        )

    async def checkpoint(self, index: int, step_id: StepId, result: CheckpointResult) -> None:
        """A checkpoint passed or failed."""
        stamp = {"run_id": self._run_id, "sequence": self._next(), "at": self._clock.now()}
        if result.passed:
            await self._sink.emit(
                CheckpointPassedEvent(**stamp, step_id=step_id, index=index, checkpoint=result)
            )
        else:
            await self._sink.emit(
                CheckpointFailedEvent(**stamp, step_id=step_id, index=index, checkpoint=result)
            )

    async def step_succeeded(self, result: StepResult) -> None:
        """A step succeeded."""
        await self._sink.emit(
            StepSucceededEvent(
                run_id=self._run_id,
                sequence=self._next(),
                at=self._clock.now(),
                step_id=result.step_id,
                index=result.index,
                duration_ms=result.duration_ms or 0,
                artifacts=result.artifacts,
            )
        )

    async def step_failed(self, result: StepResult) -> None:
        """A step failed."""
        if result.error is None:
            raise ValueError("a failed step result must carry its error")
        await self._sink.emit(
            StepFailedEvent(
                run_id=self._run_id,
                sequence=self._next(),
                at=self._clock.now(),
                step_id=result.step_id,
                index=result.index,
                duration_ms=result.duration_ms or 0,
                action_performed=result.action_performed,
                error=result.error,
                target=result.target,
                artifacts=result.artifacts,
            )
        )

    async def run_finished(self, run: Run) -> None:
        """The run ended."""
        failed = run.failed_step
        await self._sink.emit(
            RunFinishedEvent(
                run_id=self._run_id,
                sequence=self._next(),
                at=self._clock.now(),
                status=run.status,
                duration_ms=run.duration_ms or 0,
                failed_step_id=failed.step_id if failed is not None else None,
                error_type=run.error.type if run.error is not None else None,
            )
        )
