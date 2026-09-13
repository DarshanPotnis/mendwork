"""Recording a step's heal: every rung's report as it decides, and each heal's verification.

Reports are kept on the step's progress for its run record, and emitted as events at the same
moment, so live output and the record never disagree.
"""

from collections.abc import Sequence

from mendwork.engine.domain.heals import HealAttemptReport, Verification
from mendwork.engine.domain.runs import CheckpointResult
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.progress import StepProgress


class HealRecorder:
    """Keeps and emits one run's heal reports."""

    def __init__(self, emitter: RunEmitter) -> None:
        self._emitter = emitter

    async def attempted(self, progress: StepProgress, reports: Sequence[HealAttemptReport]) -> None:
        """Rungs decided."""
        for report in reports:
            progress.heal_attempts.append(report)
            await self._emitter.heal_attempted(progress.index, progress.step.id, report)

    async def verified(self, progress: StepProgress, failed: CheckpointResult | None) -> None:
        """The checkpoints after acting on the pending heal passed, or ``failed`` did not."""
        outcome = Verification.PASSED if failed is None else Verification.FAILED
        report = settle_pending(progress, outcome)
        await self._emitter.heal_verified(progress.index, progress.step.id, report, failed)


def settle_pending(progress: StepProgress, outcome: Verification) -> HealAttemptReport:
    """Replace the latest pending heal's verification with its outcome."""
    position = max(
        index
        for index, report in enumerate(progress.heal_attempts)
        if report.verification is Verification.PENDING
    )
    updated = progress.heal_attempts[position].model_copy(update={"verification": outcome})
    progress.heal_attempts[position] = updated
    return updated
