"""Human output: progress lines, the summary table, and the exact wording users rely on."""

import asyncio
import io
from datetime import UTC, datetime
from pathlib import Path

from mendwork.apps.cli.exit_codes import ExitCode, exit_code_for
from mendwork.apps.cli.human_output import (
    HumanProgress,
    describe_resolution,
    failure_lines,
    render_summary,
    trace_withheld_line,
)
from mendwork.engine.domain.enums import ActionType, SelectorStrategy
from mendwork.engine.domain.events import StepFailedEvent
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.runs import (
    ErrorCategory,
    ErrorReport,
    Run,
    RunStatus,
    SelectorOutcome,
    SelectorReport,
    StepArtifacts,
    StepResult,
    StepStatus,
    TargetEvidence,
    TraceWithheld,
    TraceWithheldReason,
    parse_artifact_name,
    parse_run_id,
)

RUN_ID = parse_run_id("20260911T141502Z-7c1e09ab")
AT = datetime(2026, 9, 11, 14, 15, 2, tzinfo=UTC)
RUNS = Path("artifacts/runs")

DRIFTED = ErrorReport(
    type="TargetDrifted",
    message="the element the recorded selectors find no longer matches the recorded identity",
    category=ErrorCategory.STEP,
    context={
        "reason": "identity_changed",
        "recorded": {"tag": "button", "role": "button", "name": "Sign in"},
        "found": {"tag": "button", "role": "button", "name": "Delete account"},
        "differences": ["accessible_name"],
    },
)
EVIDENCE = TargetEvidence(
    selectors=(
        SelectorReport(
            rank=0,
            strategy=SelectorStrategy.TEST_ID,
            level_counts=(1,),
            outcome=SelectorOutcome.HIT,
            element=0,
        ),
        SelectorReport(
            rank=1,
            strategy=SelectorStrategy.ROLE_NAME,
            level_counts=(0,),
            outcome=SelectorOutcome.NONE,
        ),
        SelectorReport(
            rank=2, strategy=SelectorStrategy.TEXT, level_counts=(0,), outcome=SelectorOutcome.NONE
        ),
        SelectorReport(
            rank=3,
            strategy=SelectorStrategy.CSS,
            level_counts=(1,),
            outcome=SelectorOutcome.HIT,
            element=0,
        ),
    ),
    resolved_rank=0,
)


def step(
    index: int, step_id: str, action: ActionType, status: StepStatus, **fields: object
) -> StepResult:
    return StepResult.model_validate(
        {"step_id": step_id, "index": index, "action": action, "status": status, **fields}
    )


def failed_run() -> Run:
    withheld = TraceWithheld(
        reason=TraceWithheldReason.SECRET_BEARING_PAGE,
        typed_at_index=2,
        typed_at_step=StepId("fill_password"),
    )
    return Run(
        run_id=RUN_ID,
        workflow_id="download_report",
        workflow_version=1,
        status=RunStatus.FAILED,
        started_at=AT,
        duration_ms=600,
        steps=(
            step(0, "open_sign_in", ActionType.NAVIGATE, StepStatus.SUCCEEDED, duration_ms=360),
            step(
                1,
                "fill_email",
                ActionType.FILL,
                StepStatus.SUCCEEDED,
                duration_ms=90,
                target=EVIDENCE,
            ),
            step(
                2,
                "fill_password",
                ActionType.FILL,
                StepStatus.SUCCEEDED,
                duration_ms=80,
                target=EVIDENCE,
            ),
            step(
                3,
                "sign_in",
                ActionType.CLICK,
                StepStatus.FAILED,
                duration_ms=70,
                target=EVIDENCE,
                error=DRIFTED,
                artifacts=StepArtifacts(
                    screenshot=parse_artifact_name("steps/004_sign_in.png"),
                    dom_snapshot=parse_artifact_name("failure/004_sign_in.dom.html"),
                    trace_withheld=withheld,
                ),
            ),
            step(4, "open_reports", ActionType.CLICK, StepStatus.NOT_RUN),
        ),
        error=DRIFTED,
    )


def test_the_withheld_trace_line_names_the_typing_step_without_blaming_the_secret() -> None:
    withheld = TraceWithheld(
        reason=TraceWithheldReason.SECRET_BEARING_PAGE,
        typed_at_index=2,
        typed_at_step=StepId("fill_password"),
    )

    assert trace_withheld_line(withheld) == (
        "trace withheld: the failing page still held a value typed at step 3 (fill_password), "
        "so the trace could contain it. Re-run with --headed to watch the failure live."
    )


def test_a_trace_deleted_after_scanning_says_so() -> None:
    withheld = TraceWithheld(reason=TraceWithheldReason.SECRET_DETECTED)

    assert trace_withheld_line(withheld).startswith(
        "trace withheld: the recorded trace contained a value typed from a secret"
    )


def test_a_drifted_failure_shows_recorded_and_found_identity_and_that_nothing_was_clicked() -> None:
    assert failure_lines(DRIFTED, EVIDENCE, action_performed=False) == [
        "      FAILED TargetDrifted: the element the recorded selectors find no longer matches "
        "the recorded identity",
        '        recorded  button "Sign in"',
        '        found     button "Delete account"',
        "        differs   accessible name",
        "      No action was performed on this step.",
    ]


def test_resolution_is_described_by_its_winning_rank_and_agreement() -> None:
    assert describe_resolution(EVIDENCE) == "target selectors[0] test_id · 2 of 4 selectors agree"


def test_the_summary_table_and_evidence_for_a_failed_run() -> None:
    assert render_summary(failed_run(), RUNS).splitlines() == [
        "",
        " #  Step           Action    Target       Checkpoints  Result     Time",
        " 1  open_sign_in   navigate  -            -            succeeded  0.36s",
        " 2  fill_email     fill      [0] test_id  -            succeeded  0.09s",
        " 3  fill_password  fill      [0] test_id  -            succeeded  0.08s",
        " 4  sign_in        click     drifted [0]  -            FAILED     0.07s",
        " 5  open_reports   click     -            -            not run    -",
        "",
        "FAILED at step 4 sign_in: TargetDrifted · 3/5 steps succeeded in 0.60s",
        f"Evidence: {RUNS / RUN_ID / 'steps/004_sign_in.png'}",
        f"          {RUNS / RUN_ID / 'failure/004_sign_in.dom.html'}",
        "          trace withheld: the failing page still held a value typed at step 3 "
        "(fill_password), so the trace could contain it. Re-run with --headed to watch the "
        "failure live.",
    ]


def test_a_step_failure_event_prints_its_failure_lines() -> None:
    stream = io.StringIO()
    event = StepFailedEvent(
        run_id=RUN_ID,
        sequence=9,
        at=AT,
        step_id=StepId("sign_in"),
        index=3,
        duration_ms=70,
        action_performed=False,
        error=DRIFTED,
        target=EVIDENCE,
        artifacts=StepArtifacts(),
    )

    asyncio.run(HumanProgress(stream, RUNS).emit(event))

    assert "No action was performed on this step." in stream.getvalue()


def test_exit_codes_distinguish_success_step_failure_and_infrastructure() -> None:
    run = failed_run()
    infrastructure = ErrorReport(
        type="BrowserUnavailable", message="gone", category=ErrorCategory.INFRASTRUCTURE
    )

    assert exit_code_for(run) is ExitCode.STEP_FAILED
    assert (
        exit_code_for(run.model_copy(update={"error": infrastructure})) is ExitCode.INFRASTRUCTURE
    )
    assert (
        exit_code_for(run.model_copy(update={"status": RunStatus.SUCCEEDED, "error": None}))
        is ExitCode.SUCCEEDED
    )
