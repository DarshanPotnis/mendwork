"""What a benchmark knows about a step: the ground truth, and what a system did (ADR 0014).

Ground truth comes from outside the product (the chaos portal's JavaScript, or a person's labels on
a real application) and reaches the engine only as these values. Nothing here reads a page or knows
which site is being measured, so the same classification serves every suite and every system.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import ActionType, VerificationStrength
from mendwork.engine.domain.identifiers import StepIdField

TargetKey = Annotated[str, StringConstraints(min_length=1, max_length=200)]
"""Names the control a step acts on, independently of its markup: ``page.control``."""
Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]
"""A mutation id, stop reason, or other short machine-readable word."""


class Expectation(StrEnum):
    """What ground truth says a system should do at a step."""

    ACT = "act"
    """The step's control is on the page and acting on it is correct."""
    ABSTAIN = "abstain"
    """Any action here is wrong: the control was removed, duplicated, or now does something else."""


class MutationCategory(StrEnum):
    """Whether a change to a page is one a system should heal through or stop at."""

    HEAL_EXPECTED = "heal_expected"
    ABSTAIN_EXPECTED = "abstain_expected"


class AppliedMutation(DomainModel):
    """One change ground truth reports on a page."""

    mutation: Label
    category: MutationCategory
    target_key: TargetKey | None = None
    """The control changed, or None for a change to the page as a whole."""


class StepTruth(DomainModel):
    """Ground truth for one step that acts on a control."""

    step_id: StepIdField
    target_key: TargetKey
    expectation: Expectation
    changes: tuple[Label, ...] = ()
    """The heal-expected changes applied to the step's control; empty when it is as recorded."""

    @property
    def target_changed(self) -> bool:
        """Whether a release changed the control this step acts on."""
        return bool(self.changes)


def step_truths(
    targets: Mapping[str, str], applied: Iterable[AppliedMutation]
) -> dict[str, StepTruth]:
    """Ground truth for every targeted step, from the changes applied to the pages it visits.

    A step expects abstention when its control received any abstain-expected change; otherwise it
    expects an action, and lists the heal-expected changes its control received.
    """
    abstain: set[str] = set()
    changes: dict[str, list[str]] = {}
    for mutation in applied:
        if mutation.target_key is None:
            continue
        if mutation.category is MutationCategory.ABSTAIN_EXPECTED:
            abstain.add(mutation.target_key)
        else:
            changes.setdefault(mutation.target_key, []).append(mutation.mutation)
    return {
        step_id: StepTruth(
            step_id=step_id,
            target_key=key,
            expectation=Expectation.ABSTAIN if key in abstain else Expectation.ACT,
            changes=tuple(sorted(changes.get(key, ()))),
        )
        for step_id, key in targets.items()
    }


class Resolution(StrEnum):
    """Where the element a system acted on came from."""

    DIRECT = "direct"
    """The recorded selectors (Mendwork's Rung 0), or a script's single locator."""
    RUNG_1 = "rung_1"
    RUNG_2 = "rung_2"
    RUNG_3 = "rung_3"

    @property
    def healed(self) -> bool:
        """Whether the element came from the heal ladder."""
        return self is not Resolution.DIRECT


def resolution_for_rung(rung: int) -> Resolution:
    """The resolution a rung stands for; Rung 0 is direct."""
    match rung:
        case 0:
            return Resolution.DIRECT
        case 1:
            return Resolution.RUNG_1
        case 2:
            return Resolution.RUNG_2
        case 3:
            return Resolution.RUNG_3
        case _:
            raise ValueError(f"no rung {rung}")


class TargetMatch(StrEnum):
    """What ground truth could say about the element an action actually reached."""

    ON_TARGET = "on_target"
    """The element was, by ground truth, the control the step should reach."""
    OFF_TARGET = "off_target"
    """Ground truth named the control, and this was a different element: a wrong action."""
    UNKNOWN = "unknown"
    """Ground truth could not name the control at that moment, so the action cannot be judged.

    On a real application a person's label is resolved on the live page, and a label that matches
    no element, or more than one, names nothing. Such an action is never counted as wrong: the
    benchmark did not see what it reached, and a measurement that cannot see must not accuse.
    """


def target_match(matched: bool, known: bool) -> TargetMatch:
    """Ground truth's verdict on one action: only a truth that spoke can say off target."""
    if matched:
        return TargetMatch.ON_TARGET
    return TargetMatch.OFF_TARGET if known else TargetMatch.UNKNOWN


class Verdict(StrEnum):
    """What the checkpoints checked right after an action said."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


class StopKind(StrEnum):
    """How a step ended, as far as classification cares."""

    COMPLETED = "completed"
    DECLINED = "declined"
    """The system decided not to act: a heal abstention, no unique target, a used-up budget."""
    APPROVAL = "approval"
    CHECKPOINT_FAILED = "checkpoint_failed"
    ERROR = "error"
    """A stop that is not a decision: an unstable page, a failed navigation, a timeout."""
    UNEXPRESSIBLE = "unexpressible"
    """A script had no locator of its kind for the step."""
    NOT_REACHED = "not_reached"


class ActionRecord(DomainModel):
    """One action a system sent at a step, checked against ground truth when it happened."""

    action: ActionType
    resolution: Resolution
    on_target: TargetMatch
    """What ground truth said about the element this action reached, where it could say anything."""
    verdict: Verdict
    during_restore: bool = False
    """Sent while restoring the page after a failed heal, so checked against a replayed step."""


class StepObservation(DomainModel):
    """What a system did at one targeted step."""

    step_id: StepIdField
    stop: StopKind
    stop_reason: Label | None = None
    actions: tuple[ActionRecord, ...] = ()
    page_wrong_actions: int = Field(default=0, ge=0)
    """Wrong actions the page itself recorded while the step ran."""
    target_available_at_stop: bool | None = None
    """Whether the real control was attached and visible when the step ended; None if not read."""
    proposal_on_target: bool | None = None
    """For a step stopped for approval: whether the proposal named the real control."""
    strength: VerificationStrength
    ladder_ran: bool = False
    duration_ms: int | None = Field(default=None, ge=0)
