"""A workflow's history as people read it: each version, the run behind it, and how strong its
checks are (ADR 0010, ADR 0013).

Checkpoint strength is a property of each step. A workflow whose steps pass only on ``url_matches``
or ``field_has_value`` is weaker than its pass rate suggests, because another element can pass those
checks, so the history says so for the latest version, step by step.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.patches import PendingPatch, step_digest
from mendwork.engine.domain.run_identifiers import RunId
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.words import change_summary, target_words
from mendwork.engine.safety.heal_policy import verification_strength


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One version, newest first in a listing."""

    version: int
    created_at: datetime
    summary: str
    run_id: RunId | None
    """The run that verified a heal version's heal."""


@dataclass(frozen=True, slots=True)
class StepStrength:
    """How strongly one step's checks prove which element was used."""

    index: int
    step_id: str
    checks: tuple[str, ...]
    strength: VerificationStrength


@dataclass(frozen=True, slots=True)
class PendingLine:
    """A pending patch that still applies to the latest version."""

    index: int
    step_id: str
    target: str
    successes: int
    runs: tuple[RunId, ...]


def history_entries(versions: Sequence[WorkflowVersion]) -> tuple[HistoryEntry, ...]:
    """Every version, newest first, with why it exists."""
    return tuple(
        HistoryEntry(
            version=version.version,
            created_at=version.created_at,
            summary=change_summary(version.change, version.steps),
            run_id=(
                version.change.evidence.run_id if isinstance(version.change, HealChange) else None
            ),
        )
        for version in reversed(versions)
    )


def step_strengths(version: WorkflowVersion) -> tuple[StepStrength, ...]:
    """Each step's checks and their strength."""
    return tuple(
        StepStrength(
            index=index,
            step_id=step.id,
            checks=tuple(item.kind for item in step.checkpoints),
            strength=verification_strength(step.checkpoints),
        )
        for index, step in enumerate(version.steps)
    )


def weak_summary(strengths: Sequence[StepStrength]) -> str | None:
    """The sentence that warns about weakly verified steps, when there are any."""
    weak = sum(1 for item in strengths if item.strength is not VerificationStrength.STRONG)
    if weak == 0:
        return None
    verb = "is" if weak == 1 else "are"
    return (
        f"{weak} of {len(strengths)} steps {verb} weakly verified or not verified: on those, a "
        "heal proves where the page went or what a field holds, not which element was used."
    )


def pending_lines(
    version: WorkflowVersion, patches: Sequence[PendingPatch]
) -> tuple[PendingLine, ...]:
    """The pending patches that still match the version's steps, in step order."""
    positions = {step.id: (index, step_digest(step)) for index, step in enumerate(version.steps)}
    lines = [
        PendingLine(
            index=positions[patch.step_id][0],
            step_id=patch.step_id,
            target=target_words(patch.change.new_target),
            successes=len(patch.successes),
            runs=tuple(success.run_id for success in patch.successes),
        )
        for patch in patches
        if patch.step_id in positions and positions[patch.step_id][1] == patch.base_step_sha256
    ]
    return tuple(sorted(lines, key=lambda line: (line.index, -line.successes)))
