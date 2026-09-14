"""A step in progress: what has happened so far, kept until the step's result is recorded."""

from dataclasses import dataclass, field
from datetime import datetime

from mendwork.engine.domain.heals import (
    AbstentionReason,
    HealAttemptReport,
    HealedRung,
    HealProposal,
    HealReport,
    RecoveryReport,
)
from mendwork.engine.domain.runs import ArtifactName, CheckpointResult, NavigationReport
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.targets import TargetEvidence
from mendwork.engine.ports.browser_types import ElementRef


@dataclass(slots=True)
class StepProgress:
    """One step's progress. A quiet step is a replay inside a restore: it emits no events."""

    index: int
    step: Step
    started_at: datetime
    started: float
    quiet: bool = False
    target: TargetEvidence | None = None
    navigation: NavigationReport | None = None
    action_performed: bool = False
    checkpoints: list[CheckpointResult] = field(default_factory=list)
    download: ArtifactName | None = None
    pinned: list[ElementRef] = field(default_factory=list)
    finished: bool = False
    heal_attempts: list[HealAttemptReport] = field(default_factory=list)
    recoveries: list[RecoveryReport] = field(default_factory=list)
    healed_rung: HealedRung | None = None
    abstention: AbstentionReason | None = None
    proposal: HealProposal | None = None

    def heal_report(self) -> HealReport | None:
        """What the heal ladder did for this step, if it ran."""
        if not self.heal_attempts:
            return None
        return HealReport(
            attempts=tuple(self.heal_attempts),
            recoveries=tuple(self.recoveries),
            healed_rung=self.healed_rung,
            abstention=self.abstention,
            proposal=self.proposal,
        )

    def forget_action(self) -> None:
        """Clear what a failed attempt did, once the page it did it on has been restored."""
        self.target = None
        self.action_performed = False
        self.checkpoints.clear()
        self.download = None
