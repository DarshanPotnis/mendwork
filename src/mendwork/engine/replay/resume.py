"""Resuming a run a person approved (ADR 0011).

A resume is another execution of the same run, in a new browser held to the egress policy in force
now. It trusts nothing it cannot see again: every step before the approved one is replayed quietly
(no events and no new results; their earlier results stand) to rebuild the page, and then the
approved step runs from its start:

1. Rung 0 runs first. If the recorded selectors find the recorded element, the step runs as
   recorded, and the proposal was not needed.
2. Otherwise the heal ladder runs without a model, and a heal may act only on the approved element,
   matched on what it is rather than where it sits. It passes the same checks and checkpoints as
   any heal; an irreversible action whose checkpoints fail leaves the run needing review.
3. If an earlier step cannot be replayed, or the ladder finds nothing or another element, nothing
   acts: the step fails with ApprovalStale, and so does the run. An old approval is never stretched
   over a changed page; a fresh run makes a fresh proposal.

The run keeps one record: the resume is a new segment, its events continue the run's sequence, its
evidence is named for its segment, and its model calls count against the run's budget. When the run
saves heals, a resume that succeeds promotes every verified heal of the run, the approved one
included, exactly as a first execution does (ADR 0013).
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import structlog

from mendwork.engine.domain.approvals import StaleReason
from mendwork.engine.domain.heals import HealProposal
from mendwork.engine.domain.identifiers import InputName
from mendwork.engine.domain.runs import STOPPING_STATUSES, Run, RunSegmentKind, StepResult
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import MendworkError, SecretUnavailable
from mendwork.engine.healing.model_rung import ModelRung
from mendwork.engine.patching.patcher import Patcher
from mendwork.engine.ports.events import EventSink
from mendwork.engine.ports.resolver import HostResolver
from mendwork.engine.replay.approval_records import resumed, settle
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.inputs import bind_inputs
from mendwork.engine.replay.journal import RunJournal, new_segment
from mendwork.engine.replay.navigation_guard import NavigationGuard
from mendwork.engine.replay.run_execution import Execution, ExecutionPorts, OpeningSteps, execute
from mendwork.engine.replay.step_runner import StepRunner
from mendwork.engine.safety.approvals import approval_stale
from mendwork.engine.safety.interruption import Interruption, ended_run
from mendwork.engine.safety.secret_scrub import SecretScrubber


@dataclass(frozen=True, slots=True)
class PreparedResume:
    """A resume that passed every check made before an approval is recorded."""

    workflow: WorkflowVersion
    proposal: HealProposal
    inputs: Mapping[InputName, str]
    guard: NavigationGuard


class Resumer:
    """Resumes approved runs."""

    def __init__(
        self,
        *,
        ports: ExecutionPorts,
        events: EventSink,
        resolver: HostResolver,
        model: ModelRung | None = None,
        patcher: Patcher | None = None,
    ) -> None:
        self._ports = ports
        self._events = events
        self._resolver = resolver
        self._model = model
        self._patcher = patcher

    async def prepare(
        self, run: Run, workflow: WorkflowVersion, proposal: HealProposal
    ) -> PreparedResume:
        """Check what a resume needs, before anything about the approval is recorded.

        Raises RunInputError, SecretUnavailable, or EgressBlocked, as a new run would: the secrets
        must be available now, and every URL known up front allowed by today's egress policy.
        """
        ports = self._ports
        inputs = bind_inputs(workflow.inputs, run.inputs)
        missing = await ports.secrets.missing(workflow.secrets)
        if missing:
            raise SecretUnavailable(
                f"the workflow needs secrets that are not available: {', '.join(missing)}",
                names=list(missing),
            )
        guard = NavigationGuard(policy=ports.egress, resolver=self._resolver)
        await guard.preflight(workflow, inputs, timeout_ms=ports.config.navigation_timeout_ms)
        return PreparedResume(workflow=workflow, proposal=proposal, inputs=inputs, guard=guard)

    async def resume(self, prepared: PreparedResume, run: Run) -> Run:
        """Execute the approved run from its approved step, and return its final record.

        The caller holds the run's claim and has recorded the approval. Step failures are reported
        in the record; an interrupt is recorded and re-raised.
        """
        ports = self._ports
        workflow, proposal = prepared.workflow, prepared.proposal
        segment = len(run.segments) + 1
        scrubber = SecretScrubber()
        started = new_segment(
            RunSegmentKind.RESUME, ports.clock.now(), ports.egress, proposal_id=proposal.id
        )
        journal = RunJournal(
            artifacts=ports.artifacts,
            clock=ports.clock,
            workflow=workflow,
            record=resumed(run, workflow, proposal, started),
            segment=segment,
            scrubber=scrubber,
        )
        emitter = RunEmitter(
            self._events, ports.clock, run.run_id, last_sequence=run.last_event_sequence
        )
        log = structlog.stdlib.get_logger("mendwork.replay").bind(
            run_id=run.run_id,
            workflow_id=workflow.workflow_id,
            segment=segment,
            proposal_id=proposal.id,
        )
        await self._begin(prepared, journal, emitter)
        log.info("run_resumed", step_index=proposal.step_index)
        index = proposal.step_index
        chooser = (
            self._model.for_run(ports.clock, reserved=run.model_usage.calls)
            if self._model is not None
            else None
        )
        patcher = self._patcher if run.source is not None else None
        saves = patcher is not None and run.source is not None and run.source.saves_heals
        execution = Execution(
            run_id=run.run_id,
            workflow=workflow,
            inputs=prepared.inputs,
            guard=prepared.guard,
            scrubber=scrubber,
            journal=journal,
            emitter=emitter,
            log=log,
            chooser=chooser,
            prior=run.steps[:index],
            prior_usage=run.model_usage,
            prior_duration_ms=run.duration_ms or 0,
            segment=segment,
            approved=proposal,
            verified=run.resume.verified_heals if run.resume is not None else (),
            proposals_made=len(run.proposals),
            downloads=frozenset(
                step.artifacts.download for step in run.steps if step.artifacts.download is not None
            ),
            first_tries=await patcher.first_tries(workflow) if saves and patcher else {},
            patcher=patcher,
        )
        return await execute(ports, execution, opening=approved_step(workflow, proposal))

    async def _begin(
        self, prepared: PreparedResume, journal: RunJournal, emitter: RunEmitter
    ) -> None:
        try:
            await journal.write(journal.record)
            await emitter.run_resumed(prepared.workflow, journal.segment, prepared.proposal)
        except asyncio.CancelledError:
            ended = ended_run(
                journal.record,
                at=self._ports.clock.now(),
                interruption=Interruption.INTERRUPT,
                step_in_progress=False,
            )
            await journal.write(settle(ended, prepared.proposal))
            raise


def approved_step(workflow: WorkflowVersion, proposal: HealProposal) -> OpeningSteps:
    """Rebuild the approved step's page by replaying the steps before it, then run it."""

    async def opening(runner: StepRunner, results: list[StepResult]) -> bool:
        index = proposal.step_index
        step = workflow.steps[index]
        for earlier, replayed in enumerate(workflow.steps[:index]):
            try:
                await runner.reestablish(earlier, replayed)
            except MendworkError as failure:
                stale = approval_stale(
                    proposal,
                    StaleReason.PAGE_NOT_REESTABLISHED,
                    detail=f"step {earlier + 1} {replayed.id} could not be replayed: "
                    f"{type(failure).__name__}: {failure.message}",
                )
                results.append(await runner.not_reestablished(index, step, stale))
                return False
        result = await runner.run(index, step, approval=proposal)
        results.append(result)
        return result.status not in STOPPING_STATUSES

    return opening
