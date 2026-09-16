"""Classifying one step against ground truth (ADR 0014).

The rules are applied in a fixed order, and the first that holds decides. A wrong action beats
everything: a step that acted on the wrong element and then recovered still acted on the wrong
element. Each wrong step is also either caught (the checkpoints right after the wrong action
failed, or never ran) or a false success (they passed), because a false success leaves nothing in a
run's record to show it.

An action ground truth could not judge is neither: it becomes ``ground_truth_unknown``, which is
counted and reported on its own. It is never wrong, because the benchmark did not see what the
action reached, and never correct, because a step cannot be called correct on an action nobody
could check. Only a real application produces these, where a person's label is resolved on the live
page; the chaos portal always answers, so its cells never land here.
"""

from enum import StrEnum
from typing import Final, Self

from pydantic import Field, model_validator

from mendwork.engine.benchmark.truth import (
    ActionRecord,
    Expectation,
    Label,
    Resolution,
    StepObservation,
    StepTruth,
    StopKind,
    TargetKey,
    TargetMatch,
    Verdict,
)
from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.identifiers import StepIdField


class OutcomeClass(StrEnum):
    """A step's outcome, one per reached targeted step."""

    HEALED_WRONG = "healed_wrong"
    DIRECT_WRONG = "direct_wrong"
    ABSTAINED_CORRECT = "abstained_correct"
    ABSTAINED_UNNECESSARY = "abstained_unnecessary"
    HEALED_CORRECT = "healed_correct"
    DIRECT_CORRECT = "direct_correct"
    FAILED = "failed"
    APPROVAL_REQUESTED = "approval_requested"
    GROUND_TRUTH_UNKNOWN = "ground_truth_unknown"
    NOT_REACHED = "not_reached"

    @property
    def wrong(self) -> bool:
        """Whether the step acted on something it should not have."""
        return self in WRONG_CLASSES

    @property
    def correct_resolution(self) -> bool:
        """Whether the step acted on exactly the right element and passed."""
        return self in (OutcomeClass.HEALED_CORRECT, OutcomeClass.DIRECT_CORRECT)


WRONG_CLASSES: Final = frozenset({OutcomeClass.HEALED_WRONG, OutcomeClass.DIRECT_WRONG})


class WrongKind(StrEnum):
    """Whether verification noticed a wrong action."""

    CAUGHT = "caught"
    FALSE_SUCCESS = "false_success"


class StepOutcome(DomainModel):
    """A classified step: what ground truth expected, what happened, and why it is in its class."""

    step_id: StepIdField
    target_key: TargetKey
    expectation: Expectation
    changes: tuple[Label, ...] = ()
    strength: VerificationStrength
    outcome: OutcomeClass
    wrong: WrongKind | None = None
    resolution: Resolution | None = None
    """Where the deciding element came from: the first wrong one, or the one the step passed on."""
    stop: StopKind
    stop_reason: Label | None = None
    ladder_ran: bool = False
    actions: int = Field(default=0, ge=0)
    wrong_actions: int = Field(default=0, ge=0)
    unknown_actions: int = Field(default=0, ge=0)
    """Actions ground truth could not judge, which are never counted as wrong."""
    proposal_on_target: bool | None = None

    @model_validator(mode="after")
    def _wrong_is_explained(self) -> Self:
        if self.outcome.wrong != (self.wrong is not None):
            raise ValueError("a wrong outcome, and only a wrong outcome, says how it was noticed")
        if self.wrong_actions > 0 and not self.outcome.wrong:
            raise ValueError("a step with a wrong action is classified wrong")
        unjudged = self.outcome is OutcomeClass.GROUND_TRUTH_UNKNOWN
        if unjudged != (self.unknown_actions > 0 and not self.outcome.wrong):
            raise ValueError(
                "an unjudged step, and only an unjudged step, is classified ground_truth_unknown"
            )
        return self

    @property
    def reached(self) -> bool:
        """Whether the run started the step."""
        return self.outcome is not OutcomeClass.NOT_REACHED

    @property
    def target_changed(self) -> bool:
        """Whether a release changed the step's control."""
        return bool(self.changes)


def classify_step(truth: StepTruth, observation: StepObservation) -> StepOutcome:
    """The step's outcome class under the ordered rules of ADR 0014."""
    if truth.step_id != observation.step_id:
        raise ValueError(f"truth for {truth.step_id} cannot classify {observation.step_id}")
    wrong = _wrong_records(truth, observation)
    wrong_count = len(wrong) + observation.page_wrong_actions
    unknown = sum(1 for record in observation.actions if record.on_target is TargetMatch.UNKNOWN)
    base: dict[str, object] = {
        "step_id": truth.step_id,
        "target_key": truth.target_key,
        "expectation": truth.expectation,
        "changes": truth.changes,
        "strength": observation.strength,
        "stop": observation.stop,
        "stop_reason": observation.stop_reason,
        "ladder_ran": observation.ladder_ran,
        "actions": len(observation.actions),
        "wrong_actions": wrong_count,
        "unknown_actions": unknown,
        "proposal_on_target": observation.proposal_on_target,
    }
    if observation.stop is StopKind.NOT_REACHED and not observation.actions and not wrong_count:
        return StepOutcome.model_validate({**base, "outcome": OutcomeClass.NOT_REACHED})
    if wrong_count:
        return _wrong_outcome(base, wrong, observation)
    if unknown:
        return StepOutcome.model_validate({**base, "outcome": OutcomeClass.GROUND_TRUTH_UNKNOWN})
    decided = _non_wrong_class(truth, observation)
    return StepOutcome.model_validate(
        {**base, "outcome": decided, "resolution": _passing_resolution(observation, decided)}
    )


def _wrong_records(truth: StepTruth, observation: StepObservation) -> list[ActionRecord]:
    """Actions that reached the wrong element, and on an abstain step every action of its own."""
    abstain = truth.expectation is Expectation.ABSTAIN
    # On an abstain step every action of the step's own is wrong whatever element it reached, so
    # ground truth need not name a control to condemn it.
    return [
        record
        for record in observation.actions
        if record.on_target is TargetMatch.OFF_TARGET or (abstain and not record.during_restore)
    ]


def _wrong_outcome(
    base: dict[str, object], wrong: list[ActionRecord], observation: StepObservation
) -> StepOutcome:
    if wrong:
        resolution: Resolution | None = wrong[0].resolution
        false_success = any(record.verdict is Verdict.PASSED for record in wrong)
    else:
        # Only the page noticed: an activation no element-level check could see, such as Enter.
        resolution = None
        false_success = observation.stop is StopKind.COMPLETED
    outcome = (
        OutcomeClass.HEALED_WRONG
        if resolution is not None and resolution.healed
        else OutcomeClass.DIRECT_WRONG
    )
    kind = WrongKind.FALSE_SUCCESS if false_success else WrongKind.CAUGHT
    return StepOutcome.model_validate(
        {**base, "outcome": outcome, "wrong": kind, "resolution": resolution}
    )


def _non_wrong_class(truth: StepTruth, observation: StepObservation) -> OutcomeClass:
    own = [record for record in observation.actions if not record.during_restore]
    match observation.stop:
        case StopKind.APPROVAL:
            return OutcomeClass.APPROVAL_REQUESTED
        case StopKind.COMPLETED if truth.expectation is Expectation.ACT and own:
            if own[-1].resolution.healed:
                return OutcomeClass.HEALED_CORRECT
            return OutcomeClass.DIRECT_CORRECT
        case StopKind.DECLINED if not own:
            if truth.expectation is Expectation.ABSTAIN:
                return OutcomeClass.ABSTAINED_CORRECT
            if observation.target_available_at_stop:
                return OutcomeClass.ABSTAINED_UNNECESSARY
            return OutcomeClass.FAILED
        case _:
            return OutcomeClass.FAILED


def _passing_resolution(observation: StepObservation, decided: OutcomeClass) -> Resolution | None:
    if not decided.correct_resolution:
        return None
    own = [record for record in observation.actions if not record.during_restore]
    return own[-1].resolution
