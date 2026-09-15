"""What the rungs share within one run, and what an accepted heal hands back."""

from dataclasses import dataclass

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import HealAttemptReport, HealedRung, ScoredCandidate
from mendwork.engine.domain.steps import Step
from mendwork.engine.healing.candidates import CandidateSignature, SignatureMatch
from mendwork.engine.healing.config import HealingConfig
from mendwork.engine.healing.model_rung import ModelChooser
from mendwork.engine.healing.scoring import ScoredElement
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.reports import identity_report
from mendwork.engine.safety.secret_scrub import SecretScrubber


@dataclass(frozen=True, slots=True)
class LadderContext:
    """The browser and settings every rung reads with."""

    browser: BrowserPort
    config: HealingConfig
    settle_timeout_ms: int
    quiet_frames: int
    scrubber: SecretScrubber
    chooser: ModelChooser | None = None
    """Rung 3's model and this run's budget; None when no model is configured."""


@dataclass(frozen=True, slots=True)
class ClimbRequest:
    """One pass through the ladder for one step."""

    step: Step
    fingerprint: Fingerprint
    attempt: int
    excluded: frozenset[CandidateSignature]
    """Signatures of candidates that already failed verification in this step."""
    deadline: Deadline
    heal_actions_used: int = 0
    """Healed targets the step already acted on, for the gates checked before asking a model."""
    reuse: SignatureMatch | None = None
    """A Rung 3 heal to find again without a model: while a page is restored, or a proposal's
    element when an approved run resumes."""
    ask_model: bool = True
    """False when an approved run resumes: an approval names an element, and a model's new pick
    could only be a different one."""


@dataclass(frozen=True, slots=True)
class AcceptedHeal:
    """A target a rung accepted. Its element stays pinned; the caller releases it."""

    rung: HealedRung
    scored: ScoredElement
    identity: ElementIdentity
    """The identity Playwright confirmed."""
    report: HealAttemptReport
    """The deciding rung's report."""

    def proposal_candidate(self, scrubber: SecretScrubber) -> ScoredCandidate:
        """The accepted candidate as evidence."""
        candidate_id = self.report.chosen or "c1"
        return scored_candidate(self.scored, candidate_id, scrubber, self.identity)


def scored_candidate(
    item: ScoredElement,
    candidate_id: str,
    scrubber: SecretScrubber,
    identity: ElementIdentity | None = None,
) -> ScoredCandidate:
    """A scored element as evidence, with its page text scrubbed of secrets."""
    return ScoredCandidate(
        id=candidate_id,
        origin=item.origin,
        identity=identity_report(identity or item.candidate.identity, scrubber),
        score=item.score,
        features=item.features,
        rejection=item.rejection,
    )
