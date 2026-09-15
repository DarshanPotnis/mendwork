"""Placing a verified heal on the latest version, which may be newer than the run's (ADR 0013).

A heal was verified against one exact step. It may become a child of the latest version only while
the latest version still has that exact step: then the heal describes it, whatever else changed.
If the latest step already targets the healed element, the heal is already applied, so a run that
ends after another run published the same heal, or a process that stopped after publishing and
before recording that it did, adds nothing. If a person rolled back this exact heal, it is not saved
again automatically. Anything else means the step changed, and the heal is stale.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from mendwork.engine.domain.changes import HealChange, Rollback
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.domain.workflow import WorkflowVersion


class Placement(StrEnum):
    """Where a heal stands against the latest version."""

    PUBLISH = "publish"
    ALREADY_APPLIED = "already_applied"
    STALE = "stale"
    PREVIOUSLY_ROLLED_BACK = "previously_rolled_back"


@dataclass(frozen=True, slots=True)
class Placed:
    """A placement, and the version it refers to: the latest, or the rollback that undid it."""

    placement: Placement
    version: int


def place(history: Sequence[WorkflowVersion], base_step: Step, new_target: Fingerprint) -> Placed:
    """Where a heal of ``base_step`` to ``new_target`` stands, given every stored version in order.

    ``history`` must not be empty.
    """
    latest = history[-1]
    current = step_named(latest, base_step.id)
    if current is None:
        return Placed(Placement.STALE, latest.version)
    if current == base_step:
        undone = undoing_rollback(history, base_step, new_target)
        if undone is not None:
            return Placed(Placement.PREVIOUSLY_ROLLED_BACK, undone)
        return Placed(Placement.PUBLISH, latest.version)
    if step_target(current) == new_target and _same_but_target(current, base_step):
        return Placed(Placement.ALREADY_APPLIED, latest.version)
    return Placed(Placement.STALE, latest.version)


def undoing_rollback(
    history: Sequence[WorkflowVersion], base_step: Step, new_target: Fingerprint
) -> int | None:
    """The rollback that undid this exact heal, if a person rolled it back."""
    old = step_target(base_step)
    for position, version in enumerate(history):
        change = version.change
        if not (
            isinstance(change, HealChange)
            and change.step_id == base_step.id
            and change.old_target == old
            and change.new_target == new_target
        ):
            continue
        for later in history[position + 1 :]:
            restored = later.change
            step = step_named(later, base_step.id)
            if (
                isinstance(restored, Rollback)
                and restored.restored_version < version.version
                and step is not None
                and step_target(step) == old
            ):
                return later.version
    return None


def step_named(version: WorkflowVersion, step_id: str) -> Step | None:
    """The version's step with this id."""
    return next((step for step in version.steps if step.id == step_id), None)


def _same_but_target(current: Step, base: Step) -> bool:
    return current.model_dump(mode="json", exclude={"target"}) == base.model_dump(
        mode="json", exclude={"target"}
    )
