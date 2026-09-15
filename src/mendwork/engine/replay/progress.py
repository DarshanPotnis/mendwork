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
from mendwork.engine.domain.patches import FoundTarget
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
    dispatching: bool = False
    """Whether the action is being sent to the page right now; if the run is interrupted then,
    whether it arrived is unknown."""
    checkpoints: list[CheckpointResult] = field(default_factory=list)
    download: ArtifactName | None = None
    pinned: list[ElementRef] = field(default_factory=list)
    finished: bool = False
    heal_attempts: list[HealAttemptReport] = field(default_factory=list)
    recoveries: list[RecoveryReport] = field(default_factory=list)
    healed_rung: HealedRung | None = None
    abstention: AbstentionReason | None = None
    proposal: HealProposal | None = None
    approval: HealProposal | None = None
    """For an approved step as its run resumes: a heal may act only on this proposal's element."""
    found: FoundTarget | None = None
    """The healed element about to be acted on, fingerprinted as the recorder would (ADR 0013)."""

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

    def verified_found(self) -> FoundTarget | None:
        """The captured element, only once its heal was verified."""
        return self.found if self.healed_rung is not None else None

    def forget_action(self) -> None:
        """Clear what a failed attempt did, once the page it did it on has been restored."""
        self.target = None
        self.action_performed = False
        self.checkpoints.clear()
        self.download = None
        self.found = None
