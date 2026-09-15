"""The replayer: starts a run of a workflow version and keeps its record.

Preflight comes first and creates nothing: inputs are bound, secrets checked, and every navigate
URL known up front held to the egress policy before a run id exists, so an invalid or refused run
leaves no artifacts behind. Then the run is claimed for this process, the exact version it executes
is saved beside its record (``workflow.json``, whose digest the record keeps), and its first
execution runs (``run_execution``). From then on every outcome ends with a final record and a
``run_finished`` event, an interrupt included.
"""

import asyncio
from collections.abc import Mapping

import structlog

from mendwork.engine.domain.runs import Run, RunSegmentKind, RunStatus
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import SecretUnavailable
from mendwork.engine.healing.model_rung import ModelRung
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.ports.browser import BrowserLauncher
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.events import EventSink
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.resolver import HostResolver
from mendwork.engine.ports.run_ids import RunIdGenerator
from mendwork.engine.ports.run_records import RunRecords
from mendwork.engine.ports.secrets import SecretResolver
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.artifact_names import WORKFLOW_SNAPSHOT
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.inputs import bind_inputs
from mendwork.engine.replay.journal import (
    RunJournal,
    new_segment,
    steps_so_far,
    workflow_snapshot,
)
from mendwork.engine.replay.navigation_guard import NavigationGuard
from mendwork.engine.replay.run_execution import Execution, ExecutionPorts, execute
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.interruption import Interruption, ended_run
from mendwork.engine.safety.secret_scrub import SecretScrubber


class Replayer:
    """Replays workflow versions step by step, acting only on verified targets."""

    def __init__(
        self,
        *,
        launcher: BrowserLauncher,
        artifacts: ArtifactStore,
        records: RunRecords,
        events: EventSink,
        secrets: SecretResolver,
        clock: Clock,
        timer: Timer,
        randomness: RandomSource,
        run_ids: RunIdGenerator,
        config: ReplayConfig,
        egress: EgressPolicy,
        resolver: HostResolver,
        model: ModelRung | None = None,
    ) -> None:
        self._launcher = launcher
        self._artifacts = artifacts
        self._records = records
        self._events = events
        self._secrets = secrets
        self._clock = clock
        self._timer = timer
        self._randomness = randomness
        self._run_ids = run_ids
        self._config = config
        self._egress = egress
        self._resolver = resolver
        self._model = model

    async def run(self, workflow: WorkflowVersion, supplied_inputs: Mapping[str, str]) -> Run:
        """Replay a workflow version with the given inputs.

        Raises RunInputError, SecretUnavailable, or EgressBlocked before the run starts. Once it
        has started, step failures are reported in the returned run, not raised; an interrupt is
        recorded (the run ends cancelled or needing review) and re-raised. An error that is not
        Mendwork's own is recorded, then raised as InfrastructureError.
        """
        inputs = bind_inputs(workflow.inputs, supplied_inputs)
        missing = await self._secrets.missing(workflow.secrets)
        if missing:
            raise SecretUnavailable(
                f"the workflow needs secrets that are not available: {', '.join(missing)}",
                names=list(missing),
            )
        guard = NavigationGuard(policy=self._egress, resolver=self._resolver)
        await guard.preflight(workflow, inputs, timeout_ms=self._config.navigation_timeout_ms)

        run_id = self._run_ids.new_run_id()
        scrubber = SecretScrubber()
        log = structlog.stdlib.get_logger("mendwork.replay").bind(
            run_id=run_id, workflow_id=workflow.workflow_id
        )
        snapshot, digest = workflow_snapshot(workflow)
        started_at = self._clock.now()
        record = Run(
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            workflow_sha256=digest,
            status=RunStatus.RUNNING,
            started_at=started_at,
            inputs=dict(inputs),
            secrets=workflow.secrets,
            steps=steps_so_far(workflow, ()),
            segments=(new_segment(RunSegmentKind.RUN, started_at, self._egress),),
        )
        journal = RunJournal(
            artifacts=self._artifacts,
            clock=self._clock,
            workflow=workflow,
            record=record,
            segment=1,
            scrubber=scrubber,
        )
        emitter = RunEmitter(self._events, self._clock, run_id)
        async with self._records.claim(run_id):
            await self._begin(workflow, snapshot, journal, emitter)
            log.info("run_started", step_count=len(workflow.steps))
            chooser = self._model.for_run(self._clock) if self._model is not None else None
            execution = Execution(
                run_id=run_id,
                workflow=workflow,
                inputs=inputs,
                guard=guard,
                scrubber=scrubber,
                journal=journal,
                emitter=emitter,
                log=log,
                chooser=chooser,
            )
            return await execute(self._ports(), execution)

    async def _begin(
        self,
        workflow: WorkflowVersion,
        snapshot: bytes,
        journal: RunJournal,
        emitter: RunEmitter,
    ) -> None:
        run_id = journal.record.run_id
        try:
            await self._artifacts.write(run_id, WORKFLOW_SNAPSHOT, snapshot)
            await journal.write(journal.record)
            await emitter.run_started(workflow)
        except asyncio.CancelledError:
            ended = ended_run(
                journal.record,
                at=self._clock.now(),
                interruption=Interruption.INTERRUPT,
                step_in_progress=False,
            )
            await journal.write(ended)
            raise

    def _ports(self) -> ExecutionPorts:
        return ExecutionPorts(
            launcher=self._launcher,
            artifacts=self._artifacts,
            secrets=self._secrets,
            clock=self._clock,
            timer=self._timer,
            randomness=self._randomness,
            config=self._config,
            egress=self._egress,
        )
