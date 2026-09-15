"""Pending patches under ``after_n_successes``: which to try first, and how a run counts (ADR 0013).

A pending patch applies to a version only while the version has the exact step the heal was
verified against, so an edit to that step retires the patch and an edit elsewhere does not. For each
step, the patch verified by the most runs is tried first, then the oldest, so the order never
depends on how the patches were stored.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.identifiers import StepId, WorkflowId
from mendwork.engine.domain.patches import (
    PatchSuccess,
    PendingPatch,
    SuccessKind,
    pending_id,
    step_digest,
)
from mendwork.engine.domain.run_identifiers import RunId
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.replay.pending_first_try import FirstTry


def first_tries(
    workflow: WorkflowVersion, pending: Sequence[PendingPatch]
) -> Mapping[StepId, tuple[FirstTry, ...]]:
    """For each step of the version, the pending targets to try before its heal ladder, in order."""
    digests = {step.id: step_digest(step) for step in workflow.steps}
    matching = sorted(
        (patch for patch in pending if digests.get(patch.step_id) == patch.base_step_sha256),
        key=lambda patch: (-len(patch.successes), patch.created_at, patch.id),
    )
    grouped: dict[StepId, list[FirstTry]] = {}
    for patch in matching:
        grouped.setdefault(patch.step_id, []).append(
            FirstTry(patch_id=patch.id, target=patch.change.new_target)
        )
    return {step_id: tuple(tries) for step_id, tries in grouped.items()}


def started(
    workflow_id: WorkflowId, step: Step, change: HealChange, run_id: RunId, at: datetime
) -> PendingPatch:
    """A new pending patch, verified once by the run that healed it."""
    base = step_digest(step)
    return PendingPatch(
        id=pending_id(step.id, base, change.new_target),
        workflow_id=workflow_id,
        step_id=step.id,
        base_step_sha256=base,
        change=change,
        successes=(PatchSuccess(run_id=run_id, at=at, how=SuccessKind.HEALED),),
        created_at=at,
    )


def counted(patch: PendingPatch, run_id: RunId, at: datetime, how: SuccessKind) -> PendingPatch:
    """The patch with one more verified run; a run that already counted counts once."""
    if any(success.run_id == run_id for success in patch.successes):
        return patch
    success = PatchSuccess(run_id=run_id, at=at, how=how)
    return patch.model_copy(update={"successes": (*patch.successes, success)})
