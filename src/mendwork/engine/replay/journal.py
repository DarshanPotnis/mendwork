"""A run's record, kept current on disk while the run is in progress (ADR 0011).

``run.json`` is rewritten, atomically, when the run starts, after every step's result, just before
an irreversible action is dispatched, and when the run finishes. If the process stops at any
moment, the record on disk says which steps finished and whether an irreversible action may have
been sent, which is what decides whether the interrupted run is cancelled or needs review.

Every write scrubs the run's inputs of the secrets resolved so far, so no intermediate record can
hold a value the final one would not.
"""

import hashlib
from collections.abc import Sequence
from datetime import datetime

from mendwork.engine.domain.runs import (
    IrreversibleDispatch,
    Run,
    RunSegment,
    RunSegmentKind,
    StepResult,
    StepStatus,
)
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.ports.clock import Clock
from mendwork.engine.replay.artifact_names import RUN_RECORD
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.secret_scrub import SecretScrubber


class RunJournal:
    """The in-progress record of one execution of a run, and the writes that keep it on disk."""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        clock: Clock,
        workflow: WorkflowVersion,
        record: Run,
        segment: int,
        scrubber: SecretScrubber,
    ) -> None:
        self._artifacts = artifacts
        self._clock = clock
        self._workflow = workflow
        self._record = record
        self._segment = segment
        self._scrubber = scrubber

    @property
    def record(self) -> Run:
        """The record as last written."""
        return self._record

    @property
    def segment(self) -> int:
        """Which execution of the run this is, counting from 1."""
        return self._segment

    async def write(self, record: Run) -> None:
        """Replace the record on disk."""
        inputs = {name: self._scrubber.scrub_text(value) for name, value in record.inputs.items()}
        self._record = record.model_copy(update={"inputs": inputs})
        await self._artifacts.write(record.run_id, RUN_RECORD, encode_run(self._record))

    async def results(self, results: Sequence[StepResult]) -> None:
        """Record every step result so far."""
        steps = steps_so_far(self._workflow, results)
        await self.write(self._record.model_copy(update={"steps": steps}))

    async def irreversible_dispatch(self, index: int, step: Step) -> None:
        """Record, before it is sent, that an irreversible action is about to be dispatched."""
        dispatch = IrreversibleDispatch(
            step_id=step.id, index=index, at=self._clock.now(), segment=self._segment
        )
        dispatched = (*self._record.irreversible_dispatched, dispatch)
        await self.write(self._record.model_copy(update={"irreversible_dispatched": dispatched}))


def new_segment(
    kind: RunSegmentKind,
    started_at: datetime,
    policy: EgressPolicy,
    *,
    proposal_id: str | None = None,
) -> RunSegment:
    """An execution starting now, with the egress policy it is held to."""
    return RunSegment(
        kind=kind,
        started_at=started_at,
        proposal_id=proposal_id,
        egress_allowed_domains=policy.allowed_domains,
        egress_loopback_exceptions=tuple(item.origin for item in policy.loopback_exceptions),
    )


def steps_so_far(
    workflow: WorkflowVersion, results: Sequence[StepResult]
) -> tuple[StepResult, ...]:
    """The results so far, followed by a not_run result for every step after them."""
    ran = list(results)
    rest = [
        StepResult(step_id=step.id, index=index, action=step.action, status=StepStatus.NOT_RUN)
        for index, step in enumerate(workflow.steps)
        if index >= len(ran)
    ]
    return tuple(ran + rest)


def encode_run(record: Run) -> bytes:
    """A run record as the bytes of ``run.json``."""
    return (record.model_dump_json(indent=2) + "\n").encode("utf-8")


def workflow_snapshot(workflow: WorkflowVersion) -> tuple[bytes, str]:
    """The exact version a run executes, as the bytes of ``workflow.json``, and their SHA-256."""
    data = (workflow.model_dump_json(indent=2) + "\n").encode("utf-8")
    return data, hashlib.sha256(data).hexdigest()
