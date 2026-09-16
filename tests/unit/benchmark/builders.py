"""Builders for the benchmark's unit tests: truths, observations, outcomes, and cells."""

from collections.abc import Sequence
from decimal import Decimal

from mendwork.engine.benchmark.cells import (
    CellResult,
    ChaosCell,
    MutationCaseCell,
    RunFailure,
    StepTiming,
    UsageMeasurement,
)
from mendwork.engine.benchmark.outcomes import StepOutcome, classify_step
from mendwork.engine.benchmark.truth import (
    ActionRecord,
    Expectation,
    MutationCategory,
    Resolution,
    StepObservation,
    StepTruth,
    StopKind,
    TargetMatch,
    Verdict,
)
from mendwork.engine.domain.enums import ActionType, VerificationStrength

STEP = "open_reports"
KEY = "dashboard.open_reports"


def truth(
    step_id: str = STEP,
    *,
    key: str = KEY,
    expectation: Expectation = Expectation.ACT,
    changes: Sequence[str] = (),
) -> StepTruth:
    return StepTruth(
        step_id=step_id, target_key=key, expectation=expectation, changes=tuple(changes)
    )


def action(
    *,
    resolution: Resolution = Resolution.DIRECT,
    on_target: TargetMatch = TargetMatch.ON_TARGET,
    verdict: Verdict = Verdict.PASSED,
    during_restore: bool = False,
    kind: ActionType = ActionType.CLICK,
) -> ActionRecord:
    return ActionRecord(
        action=kind,
        resolution=resolution,
        on_target=on_target,
        verdict=verdict,
        during_restore=during_restore,
    )


def observation(
    step_id: str = STEP,
    *,
    stop: StopKind = StopKind.COMPLETED,
    actions: Sequence[ActionRecord] = (),
    reason: str | None = None,
    page_wrong: int = 0,
    available: bool | None = None,
    strength: VerificationStrength = VerificationStrength.STRONG,
    ladder_ran: bool = False,
    duration_ms: int | None = 100,
    proposal_on_target: bool | None = None,
) -> StepObservation:
    return StepObservation(
        step_id=step_id,
        stop=stop,
        stop_reason=reason,
        actions=tuple(actions),
        page_wrong_actions=page_wrong,
        target_available_at_stop=available,
        strength=strength,
        ladder_ran=ladder_ran,
        duration_ms=duration_ms,
        proposal_on_target=proposal_on_target,
    )


def direct_correct(step_id: str = STEP, *, changes: Sequence[str] = ()) -> StepOutcome:
    return classify_step(truth(step_id, changes=changes), observation(step_id, actions=[action()]))


def healed_correct(step_id: str = STEP, *, rung: Resolution = Resolution.RUNG_2) -> StepOutcome:
    return classify_step(
        truth(step_id, changes=["synonym_rename"]),
        observation(step_id, actions=[action(resolution=rung)], ladder_ran=True),
    )


def unnecessary(step_id: str = STEP) -> StepOutcome:
    return classify_step(
        truth(step_id, changes=["synonym_rename"]),
        observation(
            step_id,
            stop=StopKind.DECLINED,
            reason="below_threshold",
            available=True,
            ladder_ran=True,
        ),
    )


def abstained(step_id: str = STEP) -> StepOutcome:
    return classify_step(
        truth(step_id, expectation=Expectation.ABSTAIN),
        observation(step_id, stop=StopKind.DECLINED, reason="top_rejected", ladder_ran=True),
    )


def false_success(step_id: str = STEP) -> StepOutcome:
    return classify_step(
        truth(step_id),
        observation(
            step_id,
            actions=[action(on_target=TargetMatch.OFF_TARGET)],
            strength=VerificationStrength.WEAK,
        ),
    )


def unjudged(step_id: str = STEP) -> StepOutcome:
    """A step whose action ground truth could not judge: a label that named nothing."""
    return classify_step(
        truth(step_id),
        observation(step_id, actions=[action(on_target=TargetMatch.UNKNOWN)]),
    )


def not_reached(step_id: str = STEP) -> StepOutcome:
    return classify_step(truth(step_id), observation(step_id, stop=StopKind.NOT_REACHED))


def failure(
    step_id: str = STEP,
    *,
    reason: str = "below_threshold",
    stop: StopKind = StopKind.DECLINED,
    action: ActionType = ActionType.CLICK,
    targeted: bool = True,
) -> RunFailure:
    """Where a run stopped: every cell that did not succeed must say."""
    return RunFailure(step_id=step_id, action=action, stop=stop, reason=reason, targeted=targeted)


def chaos_cell(
    steps: Sequence[StepOutcome],
    *,
    system: str = "ladder_free",
    workflow_id: str = "download_report",
    level: int = 5,
    seed: int = 1000,
    run_status: str = "succeeded",
    model_calls: int = 0,
    durations: Sequence[int | None] | None = None,
    usage: UsageMeasurement | None = None,
    run_failure: RunFailure | None = None,
) -> CellResult:
    times = durations if durations is not None else [100] * len(steps)
    stopped = run_failure or (None if run_status == "succeeded" else failure())
    return CellResult(
        cell=ChaosCell(workflow_id=workflow_id, level=level, seed=seed, system=system),
        run_status=run_status,
        run_failure=stopped,
        steps=tuple(steps),
        model_calls=model_calls,
        timings=tuple(
            StepTiming(step_id=step.step_id, duration_ms=duration)
            for step, duration in zip(steps, times, strict=True)
        ),
        usage=usage or UsageMeasurement(),
        duration_ms=1_000,
    )


def case_cell(
    steps: Sequence[StepOutcome],
    *,
    system: str = "ladder_free",
    mutation: str = "synonym_rename",
    run_status: str = "succeeded",
    run_failure: RunFailure | None = None,
) -> CellResult:
    return CellResult(
        cell=MutationCaseCell(
            case_id=f"{mutation}-{KEY}",
            mutation=mutation,
            category=MutationCategory.HEAL_EXPECTED,
            workflow_id="download_report",
            system=system,
        ),
        run_status=run_status,
        run_failure=run_failure or (None if run_status == "succeeded" else failure()),
        steps=tuple(steps),
        usage=UsageMeasurement(cost_usd=Decimal(0)),
    )
