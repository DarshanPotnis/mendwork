"""Reading a Mendwork run into observations: element origins, restores, verdicts, and stops."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

import pytest

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.benchmark.cells import UNRECORDED
from mendwork.engine.benchmark.observe import (
    ActionProbe,
    observe_run,
    run_failure_of,
    stop_of,
)
from mendwork.engine.benchmark.truth import (
    AppliedMutation,
    MutationCategory,
    Resolution,
    StepObservation,
    StopKind,
    TargetMatch,
    Verdict,
    step_truths,
)
from mendwork.engine.domain.enums import ActionType, CheckpointKind, VerificationStrength
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
from mendwork.engine.domain.heals import HealAttemptReport, RecoveryReport, RungOutcome
from mendwork.engine.domain.runs import (
    CheckpointResult,
    ErrorCategory,
    ErrorReport,
    Run,
    RunStatus,
    StepArtifacts,
    StepResult,
    StepStatus,
)
from mendwork.engine.domain.targets import TargetEvidence

REPO: Final = Path(__file__).resolve().parents[3]
EXAMPLE: Final = REPO / "workflows" / "examples" / "download_report.yaml"
WORKFLOW: Final = WorkflowYamlCodec(max_bytes=1 << 20).decode(EXAMPLE.read_bytes(), source="x")
TARGETS_FILE: Final = REPO / "benchmarks" / "chaos" / "workflow_targets" / "download_report.json"
TARGETS: Final[dict[str, str]] = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))["targets"]
TRUTHS: Final = step_truths(
    TARGETS,
    [
        AppliedMutation(
            mutation="synonym_rename",
            category=MutationCategory.HEAL_EXPECTED,
            target_key="dashboard.open_reports",
        )
    ],
)
RUN_ID: Final = "20260915T120000Z-0123abcd"
AT: Final = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
ORDER: Final[list[str]] = [str(step.id) for step in WORKFLOW.steps]


ON = TargetMatch.ON_TARGET
OFF = TargetMatch.OFF_TARGET


class Timeline:
    """Run events and action probes, in the order a run would produce them."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []
        self.probes: list[ActionProbe] = []

    def _stamp(self, step_id: str) -> dict[str, object]:
        return {
            "run_id": RUN_ID,
            "sequence": len(self.events) + 1,
            "at": AT,
            "step_id": step_id,
            "index": ORDER.index(step_id),
        }

    def resolved(self, step_id: str, healed_rung: int | None = None) -> None:
        """``target_resolved``: after Rung 0, or, as the replayer also emits it, after a heal."""
        evidence = TargetEvidence(selectors=(), healed_rung=healed_rung)
        self.events.append(
            TargetResolvedEvent.model_validate({**self._stamp(step_id), "evidence": evidence})
        )

    def attempted(
        self, step_id: str, rung: Literal[0, 1, 2, 3], outcome: RungOutcome, attempt: int = 1
    ) -> None:
        report = HealAttemptReport(rung=rung, attempt=attempt, outcome=outcome)
        self.events.append(
            HealAttemptedEvent.model_validate({**self._stamp(step_id), "report": report})
        )

    def verified(self, step_id: str, rung: int, *, passed: bool, attempt: int = 1) -> None:
        self.events.append(
            HealVerifiedEvent.model_validate(
                {**self._stamp(step_id), "rung": rung, "attempt": attempt, "passed": passed}
            )
        )

    def restored(self, step_id: str) -> None:
        recovery = RecoveryReport(
            after_attempt=1,
            url="http://127.0.0.1/",
            replayed=(),
            cleared_field=False,
            restored=True,
        )
        self.events.append(
            StateRestoredEvent.model_validate({**self._stamp(step_id), "recovery": recovery})
        )

    def checkpoint(self, step_id: str, *, passed: bool) -> None:
        result = CheckpointResult(index=0, kind=CheckpointKind.URL_MATCHES, passed=passed)
        kind = CheckpointPassedEvent if passed else CheckpointFailedEvent
        self.events.append(kind.model_validate({**self._stamp(step_id), "checkpoint": result}))

    def succeeded(self, step_id: str) -> None:
        self.events.append(
            StepSucceededEvent.model_validate(
                {**self._stamp(step_id), "duration_ms": 5, "artifacts": StepArtifacts()}
            )
        )

    def act(self, step_id: str, *keys: str, known: bool = True) -> None:
        self.probes.append(
            ActionProbe.model_validate(
                {
                    "step_id": step_id,
                    "action": ActionType.CLICK,
                    "event_position": len(self.events),
                    "matched_keys": keys,
                    "ground_truth_known": known,
                }
            )
        )


def not_run(index: int) -> StepResult:
    step = WORKFLOW.steps[index]
    return StepResult(step_id=step.id, index=index, action=step.action, status=StepStatus.NOT_RUN)


def run(results: dict[str, StepResult], status: RunStatus = RunStatus.FAILED) -> Run:
    return Run.model_validate(
        {
            "run_id": RUN_ID,
            "workflow_id": "download_report",
            "workflow_version": 1,
            "status": status,
            "started_at": AT,
            "steps": tuple(
                results.get(step_id, not_run(index)) for index, step_id in enumerate(ORDER)
            ),
        }
    )


def result(step_id: str, status: StepStatus, error: ErrorReport | None = None) -> StepResult:
    index = ORDER.index(step_id)
    return StepResult.model_validate(
        {
            "step_id": step_id,
            "index": index,
            "action": WORKFLOW.steps[index].action,
            "status": status,
            "duration_ms": 250,
            "error": error,
        }
    )


def error(kind: str, reason: str | None = None) -> ErrorReport:
    context = {} if reason is None else {"reason": reason}
    return ErrorReport(type=kind, message="m", category=ErrorCategory.STEP, context=context)


def observed(timeline: Timeline, results: dict[str, StepResult]) -> dict[str, StepObservation]:
    observations = observe_run(
        run(results),
        WORKFLOW,
        timeline.events,
        timeline.probes,
        TRUTHS,
        available={},
        page_wrong={},
    )
    return {str(item.step_id): item for item in observations}


FAILED = StepStatus.FAILED


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        (None, (StopKind.NOT_REACHED, None)),
        (result("sign_in", StepStatus.NOT_RUN), (StopKind.NOT_REACHED, None)),
        (result("sign_in", StepStatus.SUCCEEDED), (StopKind.COMPLETED, None)),
        (
            result("sign_in", StepStatus.AWAITING_APPROVAL),
            (StopKind.APPROVAL, "approval_required"),
        ),
        (
            result("sign_in", FAILED, error("HealAbstained", "below_threshold")),
            (StopKind.DECLINED, "below_threshold"),
        ),
        (
            result("sign_in", FAILED, error("HealAbstained", "page_never_stable")),
            (StopKind.ERROR, "page_never_stable"),
        ),
        (
            result("sign_in", FAILED, error("HealAbstained", "heal_timed_out")),
            (StopKind.ERROR, "heal_timed_out"),
        ),
        (result("sign_in", FAILED, error("TargetNotFound")), (StopKind.DECLINED, "TargetNotFound")),
        (result("sign_in", FAILED, error("BudgetExceeded", "run")), (StopKind.DECLINED, "run")),
        (
            result("sign_in", FAILED, error("CheckpointFailed", "url_mismatch")),
            (StopKind.CHECKPOINT_FAILED, "url_mismatch"),
        ),
        (result("sign_in", FAILED, error("EgressBlocked")), (StopKind.ERROR, "EgressBlocked")),
        (result("sign_in", StepStatus.NEEDS_REVIEW), (StopKind.ERROR, "needs_review")),
    ],
)
def test_each_way_a_step_ends_maps_to_a_stop(
    step: StepResult | None, expected: tuple[StopKind, str | None]
) -> None:
    assert stop_of(step) == expected


def test_an_action_after_rung_0_resolved_is_direct_and_its_checkpoints_decide_its_verdict() -> None:
    timeline = Timeline()
    timeline.resolved("sign_in")
    timeline.act("sign_in", "login.sign_in")
    timeline.checkpoint("sign_in", passed=True)
    timeline.succeeded("sign_in")

    step = observed(timeline, {"sign_in": result("sign_in", StepStatus.SUCCEEDED)})["sign_in"]

    assert step.stop is StopKind.COMPLETED
    assert [(a.resolution, a.on_target, a.verdict) for a in step.actions] == [
        (Resolution.DIRECT, ON, Verdict.PASSED)
    ]
    assert (step.strength, step.ladder_ran, step.duration_ms) == (
        VerificationStrength.WEAK,
        False,
        250,
    )


def test_a_failed_heal_its_restore_and_the_next_heal_are_lined_up_with_ground_truth() -> None:
    timeline = Timeline()
    timeline.attempted("open_reports", 0, RungOutcome.NOT_FOUND)
    timeline.attempted("open_reports", 2, RungOutcome.RESOLVED)
    timeline.act("open_reports", "dashboard.nav_reports")
    timeline.checkpoint("open_reports", passed=False)
    timeline.verified("open_reports", 2, passed=False)
    timeline.act("open_reports", "login.sign_in")
    timeline.act("open_reports", "reports.download_csv")
    timeline.restored("open_reports")
    timeline.attempted("open_reports", 3, RungOutcome.RESOLVED, attempt=2)
    timeline.act("open_reports", "dashboard.open_reports")
    timeline.verified("open_reports", 3, passed=True, attempt=2)
    timeline.succeeded("open_reports")

    results = {"open_reports": result("open_reports", StepStatus.SUCCEEDED)}
    step = observed(timeline, results)["open_reports"]

    assert [(a.resolution, a.on_target, a.verdict, a.during_restore) for a in step.actions] == [
        (Resolution.RUNG_2, OFF, Verdict.FAILED, False),
        (Resolution.RUNG_2, ON, Verdict.NOT_CHECKED, True),
        (Resolution.RUNG_2, OFF, Verdict.NOT_CHECKED, True),
        (Resolution.RUNG_3, ON, Verdict.PASSED, False),
    ]
    assert step.ladder_ran


def test_events_of_other_steps_never_decide_where_an_element_came_from() -> None:
    timeline = Timeline()
    timeline.attempted("sign_in", 2, RungOutcome.RESOLVED)
    timeline.act("open_reports", "dashboard.open_reports")

    step = observed(timeline, {})["open_reports"]

    assert step.actions[0].resolution is Resolution.DIRECT
    assert step.actions[0].verdict is Verdict.NOT_CHECKED
    assert not step.ladder_ran


def test_a_verdict_is_read_only_up_to_the_next_decision_on_the_step() -> None:
    timeline = Timeline()
    timeline.resolved("apply_filter")
    timeline.act("apply_filter", "reports.apply_filter")
    timeline.attempted("apply_filter", 0, RungOutcome.DRIFTED)
    timeline.checkpoint("apply_filter", passed=True)
    timeline.resolved("download_csv")
    timeline.act("download_csv", "reports.download_csv")
    timeline.checkpoint("download_csv", passed=True)

    steps = observed(timeline, {})

    assert steps["apply_filter"].actions[0].verdict is Verdict.NOT_CHECKED
    assert steps["download_csv"].actions[0].verdict is Verdict.PASSED


def test_every_targeted_step_is_observed_in_order_with_what_was_read_at_its_stop() -> None:
    results = {
        "fill_email": result("fill_email", StepStatus.SUCCEEDED),
        "fill_password": result("fill_password", FAILED, error("HealAbstained", "below_threshold")),
    }

    steps = observe_run(
        run(results),
        WORKFLOW,
        [],
        [],
        TRUTHS,
        available={"fill_password": True},
        page_wrong={"fill_email": 1},
    )

    assert [str(step.step_id) for step in steps] == list(TARGETS)
    by_id = {str(step.step_id): step for step in steps}
    assert (by_id["fill_password"].stop, by_id["fill_password"].target_available_at_stop) == (
        StopKind.DECLINED,
        True,
    )
    assert by_id["fill_email"].page_wrong_actions == 1
    assert by_id["download_csv"].stop is StopKind.NOT_REACHED
    assert by_id["download_csv"].strength is VerificationStrength.STRONG


def test_a_heal_is_read_from_the_rung_target_resolved_names_and_rung_0_after_a_restore() -> None:
    # The replayer's own order: the ladder reports, then target_resolved names the healed rung;
    # after a restore, Rung 0 runs again and target_resolved names no rung.
    timeline = Timeline()
    timeline.attempted("open_reports", 0, RungOutcome.NOT_FOUND)
    timeline.attempted("open_reports", 2, RungOutcome.RESOLVED)
    timeline.resolved("open_reports", healed_rung=2)
    timeline.act("open_reports", "dashboard.nav_reports")
    timeline.checkpoint("open_reports", passed=False)
    timeline.verified("open_reports", 2, passed=False)
    timeline.restored("open_reports")
    timeline.resolved("open_reports")
    timeline.act("open_reports", "dashboard.open_reports")
    timeline.checkpoint("open_reports", passed=True)
    timeline.succeeded("open_reports")

    results = {"open_reports": result("open_reports", StepStatus.SUCCEEDED)}
    step = observed(timeline, results)["open_reports"]

    assert [(a.resolution, a.on_target, a.verdict, a.during_restore) for a in step.actions] == [
        (Resolution.RUNG_2, OFF, Verdict.FAILED, False),
        (Resolution.DIRECT, ON, Verdict.PASSED, False),
    ]
    assert step.ladder_ran


def test_an_action_whose_ground_truth_could_not_answer_is_unjudged_not_off_target() -> None:
    timeline = Timeline()
    timeline.resolved("sign_in")
    timeline.act("sign_in", known=False)
    timeline.checkpoint("sign_in", passed=True)
    timeline.succeeded("sign_in")

    step = observed(timeline, {"sign_in": result("sign_in", StepStatus.SUCCEEDED)})["sign_in"]

    assert [action.on_target for action in step.actions] == [TargetMatch.UNKNOWN]


def test_ground_truth_that_answered_and_named_another_element_is_off_target() -> None:
    timeline = Timeline()
    timeline.resolved("sign_in")
    timeline.act("sign_in")
    timeline.checkpoint("sign_in", passed=True)
    timeline.succeeded("sign_in")

    step = observed(timeline, {"sign_in": result("sign_in", StepStatus.SUCCEEDED)})["sign_in"]

    assert [action.on_target for action in step.actions] == [TargetMatch.OFF_TARGET]


def failed(step_id: str, error_type: str, reason: str | None = None) -> StepResult:
    context = {"reason": reason} if reason is not None else {}
    return result(
        step_id,
        StepStatus.FAILED,
        ErrorReport(type=error_type, message="m", category=ErrorCategory.STEP, context=context),
    )


def test_a_successful_run_stopped_nowhere() -> None:
    done = {step_id: result(step_id, StepStatus.SUCCEEDED) for step_id in ORDER}

    assert run_failure_of(run(done, RunStatus.SUCCEEDED), WORKFLOW) is None


def test_a_run_that_failed_at_a_navigate_step_still_says_why() -> None:
    """A navigate acts on no control, so nothing classifies it; the cell must report it anyway."""
    first = WORKFLOW.steps[0]
    assert first.action is ActionType.NAVIGATE

    failure = run_failure_of(run({ORDER[0]: failed(ORDER[0], "NavigationFailed")}), WORKFLOW)

    assert failure is not None
    assert (str(failure.step_id), failure.targeted) == (ORDER[0], False)
    assert (failure.stop, failure.reason) == (StopKind.ERROR, "NavigationFailed")


def test_a_run_that_failed_reports_the_first_step_that_did_not_complete() -> None:
    results = {
        ORDER[0]: result(ORDER[0], StepStatus.SUCCEEDED),
        "fill_email": result("fill_email", StepStatus.SUCCEEDED),
        "fill_password": failed("fill_password", "HealAbstained", "below_margin"),
    }

    failure = run_failure_of(run(results), WORKFLOW)

    assert failure is not None
    assert (str(failure.step_id), failure.reason, failure.targeted) == (
        "fill_password",
        "below_margin",
        True,
    )


def test_a_run_that_failed_with_nothing_to_show_for_it_is_named_unrecorded() -> None:
    """A failure the benchmark cannot explain must be visible, never absent."""
    done = {step_id: result(step_id, StepStatus.SUCCEEDED) for step_id in ORDER}

    failure = run_failure_of(run(done), WORKFLOW)

    assert failure is not None
    assert failure.reason == UNRECORDED
