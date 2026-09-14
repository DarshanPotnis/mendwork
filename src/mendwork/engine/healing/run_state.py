"""What one run remembers for healing: where each step started, and each step's verified heal.

Created for each run and handed to the step runner, never shared between runs.

- **Starts** record the document and URL each step began on. The steps that began on the
  same document as a failed step are the segment a restore replays: re-opening the first
  one's URL and replaying them rebuilds the page the failed step acted on.
- **Heal actions** count how many healed targets each step acted on, for the attempt limits.
- **Verified heals** remember the signature of each step's proven heal and the rung that found
  it, so a restore can replay that step without it counting as a new heal attempt, and a
  Rung 3 heal without asking a model again.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from mendwork.engine.domain.heals import HealedRung
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.steps import Step
from mendwork.engine.healing.candidates import CandidateSignature


@dataclass(frozen=True, slots=True)
class StepStart:
    """The page a step began on."""

    index: int
    step: Step
    document: str
    url: str


@dataclass(frozen=True, slots=True)
class VerifiedHeal:
    """A step's heal that passed its checkpoints."""

    signature: CandidateSignature
    rung: HealedRung


class RunHealState:
    """One run's healing memory."""

    def __init__(self) -> None:
        self._starts: dict[int, StepStart] = {}
        self._heal_actions: dict[StepId, int] = {}
        self._verified: dict[StepId, VerifiedHeal] = {}

    def started(self, start: StepStart) -> None:
        """Record the page a step began on."""
        self._starts[start.index] = start

    def segment(self, index: int) -> tuple[StepStart, ...]:
        """The steps up to and including ``index`` that began on its document, in order."""
        current = self._starts[index]
        first = index
        while first - 1 in self._starts and self._starts[first - 1].document == current.document:
            first -= 1
        return tuple(self._starts[position] for position in range(first, index + 1))

    def rebased(self, starts: Sequence[StepStart], document: str) -> None:
        """After a restore, the segment's steps began on the re-opened document."""
        for start in starts:
            self._starts[start.index] = replace(start, document=document)

    def heal_actions(self, step_id: StepId) -> int:
        """How many healed targets the step has acted on in this run."""
        return self._heal_actions.get(step_id, 0)

    def count_heal_action(self, step_id: StepId) -> None:
        """The step acted on a healed target."""
        self._heal_actions[step_id] = self.heal_actions(step_id) + 1

    def verified(self, step_id: StepId) -> VerifiedHeal | None:
        """The step's verified heal, if it has one."""
        return self._verified.get(step_id)

    def remember_verified(
        self, step_id: StepId, signature: CandidateSignature, rung: HealedRung
    ) -> None:
        """The step's heal passed its checkpoints."""
        self._verified[step_id] = VerifiedHeal(signature, rung)
