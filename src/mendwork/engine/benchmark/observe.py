"""Reading what Mendwork did at each step from its run record and events (ADR 0014).

A benchmark wraps the browser so that, just before every action, it notes how many run events had
been emitted and which controls the element really was. Lined up with the events, that says where
the element came from (the latest ``target_resolved`` or resolved ``heal_attempted``), whether the
action was part of restoring the page after a failed heal (between a failed ``heal_verified`` and
``state_restored``), and what the checkpoints right after it said.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import Field

from mendwork.engine.benchmark.cells import UNRECORDED, RunFailure
from mendwork.engine.benchmark.truth import (
    ActionRecord,
    Resolution,
    StepObservation,
    StepTruth,
    StopKind,
    TargetKey,
    Verdict,
    resolution_for_rung,
    target_match,
)
from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.events import (
    CheckpointFailedEvent,
    CheckpointPassedEvent,
    HealAttemptedEvent,
    HealVerifiedEvent,
    RunEvent,
    StateRestoredEvent,
    StepSucceededEvent,
    TargetResolvedEvent,
)
from mendwork.engine.domain.heals import AbstentionReason, RungOutcome
from mendwork.engine.domain.identifiers import StepIdField
from mendwork.engine.domain.runs import Run, RunStatus, StepResult, StepStatus
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.safety.heal_policy import verification_strength

DECLINING_ERRORS: Final = frozenset(
    {"HealAbstained", "TargetNotFound", "AmbiguousTarget", "TargetDrifted", "BudgetExceeded"}
)
"""Errors that mean Mendwork decided not to act."""
NOT_DECISIONS: Final = frozenset(
    {AbstentionReason.PAGE_NEVER_STABLE.value, AbstentionReason.HEAL_TIMED_OUT.value}
)
"""Abstention reasons that report a page or a clock, not a judgement about the element."""


class ActionProbe(DomainModel):
    """One action, noted by the benchmark's browser wrapper just before it was sent."""

    step_id: StepIdField
    action: ActionType
    event_position: int = Field(ge=0)
    """How many run events had been emitted when the action was about to be sent."""
    matched_keys: tuple[TargetKey, ...] = ()
    """Every control the element was, by ground truth, at that moment."""
    ground_truth_known: bool = True
    """Whether ground truth could name the step's control then; False leaves the action unjudged.

    The chaos portal always answers, so its probes leave this true. A person's label on a real
    application can fail to name anything at a given moment, and then the action is not judged.
    """


def stop_of(result: StepResult | None) -> tuple[StopKind, str | None]:
    """How a step ended, and the reason Mendwork gave."""
    if result is None or result.status is StepStatus.NOT_RUN:
        return StopKind.NOT_REACHED, None
    if result.status is StepStatus.SUCCEEDED:
        return StopKind.COMPLETED, None
    if result.status is StepStatus.AWAITING_APPROVAL:
        return StopKind.APPROVAL, "approval_required"
    error = result.error
    if error is None:
        return StopKind.ERROR, result.status.value
    reason = error.context.get("reason")
    label = str(reason) if isinstance(reason, str) else error.type
    if error.type == "CheckpointFailed":
        return StopKind.CHECKPOINT_FAILED, label
    if error.type in DECLINING_ERRORS and label not in NOT_DECISIONS:
        return StopKind.DECLINED, label
    return StopKind.ERROR, label


def run_failure_of(run: Run, workflow: WorkflowVersion) -> RunFailure | None:
    """The step a run stopped at and why, or None when every step succeeded.

    This covers steps ``observe_run`` never classifies, such as a navigate that could not load the
    page: those act on no control, so without this a run could fail leaving no record of why.
    """
    if run.status is RunStatus.SUCCEEDED:
        return None
    results = {result.step_id: result for result in run.steps}
    targeted = {step.id for step in workflow.steps if _acts_on_a_control(step)}
    for step in workflow.steps:
        stop, reason = stop_of(results.get(step.id))
        if stop is StopKind.COMPLETED:
            continue
        if stop is StopKind.NOT_REACHED and results.get(step.id) is not None:
            continue
        return RunFailure(
            step_id=step.id,
            action=step.action,
            stop=stop,
            reason=reason or UNRECORDED,
            targeted=step.id in targeted,
        )
    # The run did not succeed, yet no step reports a stop: say so rather than say nothing.
    last = workflow.steps[-1]
    return RunFailure(
        step_id=last.id,
        action=last.action,
        stop=StopKind.ERROR,
        reason=UNRECORDED,
        targeted=last.id in targeted,
    )


def _acts_on_a_control(step: Step) -> bool:
    return step.action is not ActionType.NAVIGATE


def observe_run(
    run: Run,
    workflow: WorkflowVersion,
    events: Sequence[RunEvent],
    probes: Sequence[ActionProbe],
    truths: Mapping[str, StepTruth],
    *,
    available: Mapping[str, bool],
    page_wrong: Mapping[str, int],
) -> tuple[StepObservation, ...]:
    """An observation for every targeted step of the workflow, in step order."""
    results = {result.step_id: result for result in run.steps}
    order = [step.id for step in workflow.steps]
    observations: list[StepObservation] = []
    for step in workflow.steps:
        truth = truths.get(step.id)
        if truth is None:
            continue
        mine = [
            (position, event) for position, event in enumerate(events) if _step_of(event) == step.id
        ]
        earlier_keys = {
            truths[step_id].target_key
            for step_id in order[: order.index(step.id) + 1]
            if step_id in truths
        }
        records = tuple(
            _record(probe, truth, mine, earlier_keys)
            for probe in probes
            if probe.step_id == step.id
        )
        stop, reason = stop_of(results.get(step.id))
        result = results.get(step.id)
        observations.append(
            StepObservation(
                step_id=step.id,
                stop=stop,
                stop_reason=reason,
                actions=records,
                page_wrong_actions=page_wrong.get(step.id, 0),
                target_available_at_stop=available.get(step.id),
                strength=verification_strength(step.checkpoints),
                ladder_ran=any(
                    isinstance(event, HealAttemptedEvent) and event.report.rung >= 1
                    for _, event in mine
                ),
                duration_ms=result.duration_ms if result is not None else None,
            )
        )
    return tuple(observations)


def _step_of(event: RunEvent) -> str | None:
    step_id: object = getattr(event, "step_id", None)
    return step_id if isinstance(step_id, str) else None


def _record(
    probe: ActionProbe,
    truth: StepTruth,
    mine: Sequence[tuple[int, RunEvent]],
    earlier_keys: set[str],
) -> ActionRecord:
    before = [event for position, event in mine if position < probe.event_position]
    after = [event for position, event in mine if position >= probe.event_position]
    during_restore = _restoring(before)
    if during_restore:
        matched = bool(set(probe.matched_keys) & earlier_keys)
    else:
        matched = truth.target_key in probe.matched_keys
    return ActionRecord(
        action=probe.action,
        resolution=_resolution(before),
        on_target=target_match(matched, probe.ground_truth_known),
        verdict=Verdict.NOT_CHECKED if during_restore else _verdict(after),
        during_restore=during_restore,
    )


def _restoring(before: Sequence[RunEvent]) -> bool:
    for event in reversed(before):
        if isinstance(event, StateRestoredEvent):
            return False
        if isinstance(event, HealVerifiedEvent):
            return not event.passed
    return False


def _resolution(before: Sequence[RunEvent]) -> Resolution:
    for event in reversed(before):
        if isinstance(event, TargetResolvedEvent):
            # The replayer resolves a healed target too, and says which rung found it.
            return resolution_for_rung(event.evidence.healed_rung or 0)
        if isinstance(event, HealAttemptedEvent) and event.report.outcome is RungOutcome.RESOLVED:
            return resolution_for_rung(event.report.rung)
    return Resolution.DIRECT


def _verdict(after: Sequence[RunEvent]) -> Verdict:
    """What the checkpoints right after the action said, up to the next decision on the step."""
    passed = False
    for event in after:
        if isinstance(event, HealVerifiedEvent):
            return Verdict.PASSED if event.passed else Verdict.FAILED
        if isinstance(event, CheckpointFailedEvent):
            return Verdict.FAILED
        if isinstance(event, CheckpointPassedEvent):
            passed = True
        if isinstance(event, StepSucceededEvent):
            return Verdict.PASSED
        if isinstance(event, TargetResolvedEvent | HealAttemptedEvent | StateRestoredEvent):
            break
    return Verdict.PASSED if passed else Verdict.NOT_CHECKED
