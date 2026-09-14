"""Readable run output: progress as events arrive, then a summary table.

Presentation only: every fact shown comes from the run's events and record.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from pydantic import JsonValue

from mendwork.apps.cli.heal_output import (
    DIFFERENCE_WORDS,
    attempt_lines,
    next_step,
    resolution_line,
    restored_lines,
    stop_headline,
    verified_lines,
)
from mendwork.apps.cli.heal_words import INDENT
from mendwork.apps.cli.model_output import model_usage_line
from mendwork.engine.domain.enums import ActionType, ValueKind
from mendwork.engine.domain.events import (
    ActionPerformedEvent,
    CheckpointFailedEvent,
    CheckpointPassedEvent,
    HealAttemptedEvent,
    HealVerifiedEvent,
    RunEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateRestoredEvent,
    StepFailedEvent,
    StepStartedEvent,
    StepSucceededEvent,
    TargetResolvedEvent,
)
from mendwork.engine.domain.runs import (
    ErrorReport,
    Run,
    RunStatus,
    StepResult,
    StepStatus,
    TraceWithheld,
    TraceWithheldReason,
)
from mendwork.engine.domain.targets import SelectorOutcome, TargetEvidence

_MODEL_RUNG = 3
_STOPPED_WORDS = {
    RunStatus.AWAITING_APPROVAL: "AWAITING APPROVAL",
    RunStatus.NEEDS_REVIEW: "NEEDS REVIEW",
}
_RESULT_WORDS = {
    StepStatus.SUCCEEDED: "succeeded",
    StepStatus.FAILED: "FAILED",
    StepStatus.AWAITING_APPROVAL: "AWAITING APPROVAL",
    StepStatus.NEEDS_REVIEW: "NEEDS REVIEW",
}


class HumanProgress:
    """An EventSink that prints each step as it happens."""

    def __init__(self, stream: TextIO, run_directory: Path) -> None:
        self._stream = stream
        self._run_directory = run_directory
        self._step_count = 0

    async def emit(self, event: RunEvent) -> None:
        for line in self._lines(event):
            self._stream.write(line + "\n")
        self._stream.flush()

    def _lines(self, event: RunEvent) -> list[str]:
        match event:
            case RunStartedEvent():
                self._step_count = event.step_count
                steps = "step" if event.step_count == 1 else "steps"
                return [
                    f"Run {event.run_id} · {event.workflow_id} v{event.workflow_version} · "
                    f"{event.step_count} {steps}",
                    f"Artifacts: {self._run_directory / event.run_id}",
                    "",
                ]
            case StepStartedEvent():
                return [f"[{event.index + 1}/{self._step_count}] {event.step_id} · {event.action}"]
            case TargetResolvedEvent():
                return [INDENT + describe_resolution(event.evidence)]
            case HealAttemptedEvent():
                return attempt_lines(event.report)
            case HealVerifiedEvent():
                return verified_lines(event)
            case StateRestoredEvent():
                return restored_lines(event)
            case ActionPerformedEvent():
                return _action_lines(event)
            case CheckpointPassedEvent():
                detail = f" {event.checkpoint.detail}" if event.checkpoint.detail else ""
                return [f"{INDENT}passed {event.checkpoint.kind}{detail}"]
            case CheckpointFailedEvent():
                detail = f" ({event.checkpoint.detail})" if event.checkpoint.detail else ""
                return [
                    f"{INDENT}FAILED {event.checkpoint.kind}: {event.checkpoint.reason}{detail}"
                ]
            case StepFailedEvent():
                return failure_lines(event.error, event.target, event.action_performed)
            case StepSucceededEvent() | RunFinishedEvent():
                return []


def describe_resolution(evidence: TargetEvidence) -> str:
    """One line on how Rung 0 found a target, or which rung healed it."""
    healed = resolution_line(evidence)
    if healed is not None:
        return healed
    total = len(evidence.selectors)
    hits = [report for report in evidence.selectors if report.outcome is SelectorOutcome.HIT]
    if evidence.resolved_rank is None:
        return f"target: {len(hits)} of {total} selectors hit"
    winner = evidence.selectors[evidence.resolved_rank]
    return (
        f"target selectors[{winner.rank}] {winner.strategy} · "
        f"{len(hits)} of {total} selectors agree"
    )


def _action_lines(event: ActionPerformedEvent) -> list[str]:
    if event.navigation is not None:
        status = f" ({event.navigation.status})" if event.navigation.status is not None else ""
        attempts = event.navigation.attempts
        retried = f" after {attempts} attempts" if attempts > 1 else ""
        return [f"{INDENT}loaded {event.navigation.url}{status}{retried}"]
    if event.action is ActionType.FILL and event.value_kind is not None:
        article = "an" if event.value_kind is ValueKind.INPUT else "a"
        return [f"{INDENT}typed {article} {event.value_kind} value"]
    return []


def failure_lines(
    error: ErrorReport, target: TargetEvidence | None, action_performed: bool
) -> list[str]:
    """Why a step failed, with the evidence that matters for its kind of failure."""
    lines = [INDENT + stop_headline(error)]
    context = error.context
    recorded = _identity(context.get("recorded"))
    found = _identity(context.get("found"))
    if recorded is not None and found is not None:
        lines.append(f"{INDENT}  recorded  {recorded}")
        lines.append(f"{INDENT}  found     {found}")
        differences = context.get("differences")
        if isinstance(differences, list) and differences:
            words = ", ".join(DIFFERENCE_WORDS.get(str(item), str(item)) for item in differences)
            lines.append(f"{INDENT}  differs   {words}")
    if target is not None and error.type in {"AmbiguousTarget", "TargetNotFound"}:
        lines.extend(_selector_lines(target))
    if action_performed:
        lines.append(f"{INDENT}The action was performed before the step failed.")
    else:
        lines.append(f"{INDENT}No action was performed on this step.")
    guidance = next_step(error)
    if guidance is not None:
        lines.append(INDENT + guidance)
    return lines


def _selector_lines(target: TargetEvidence) -> list[str]:
    lines: list[str] = []
    for report in target.selectors:
        counts = "/".join(str(count) for count in report.level_counts)
        if report.outcome is SelectorOutcome.HIT:
            element = (
                target.elements[report.element]
                if report.element is not None and report.element < len(target.elements)
                else None
            )
            where = f" → {_describe(element.role, element.name, element.tag)}" if element else ""
            lines.append(
                f"{INDENT}  selectors[{report.rank}] {report.strategy}: one element{where}"
            )
        elif report.outcome is SelectorOutcome.MANY:
            lines.append(f"{INDENT}  selectors[{report.rank}] {report.strategy}: {counts} matches")
        else:
            lines.append(f"{INDENT}  selectors[{report.rank}] {report.strategy}: nothing")
    return lines


def _identity(value: JsonValue | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    role = value.get("role")
    name = value.get("name")
    tag = value.get("tag")
    return _describe(
        role if isinstance(role, str) else None,
        name if isinstance(name, str) else "",
        tag if isinstance(tag, str) else "element",
    )


def _describe(role: str | None, name: str, tag: str) -> str:
    return f'{role or tag} "{name}"'


def render_summary(run: Run, run_directory: Path) -> str:
    """The summary table and outcome that end human output."""
    rows = [("#", "Step", "Action", "Target", "Checkpoints", "Result", "Time")]
    rows.extend(_row(step) for step in run.steps)
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    table = [
        " " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]
    return "\n".join(["", *table, "", *_outcome(run, run_directory)])


def _row(step: StepResult) -> tuple[str, str, str, str, str, str, str]:
    if step.status is StepStatus.NOT_RUN:
        return (str(step.index + 1), step.step_id, step.action, "-", "-", "not run", "-")
    passed = sum(1 for checkpoint in step.checkpoints if checkpoint.passed)
    total = len(step.checkpoints)
    checks = f"{passed}/{total}" if total else "-"
    seconds = f"{(step.duration_ms or 0) / 1000:.2f}s"
    abstained = step.error is not None and step.error.type == "HealAbstained"
    result = "ABSTAINED" if abstained else _RESULT_WORDS.get(step.status, step.status.value)
    return (
        str(step.index + 1),
        step.step_id,
        step.action,
        _target_cell(step),
        checks,
        result,
        seconds,
    )


def _target_cell(step: StepResult) -> str:
    target = step.target
    error_type = step.error.type if step.error is not None else None
    heal = step.heal
    if heal is not None:
        if heal.healed_rung is not None:
            model = " (model)" if heal.healed_rung == _MODEL_RUNG else ""
            return f"healed r{heal.healed_rung}{model}"
        if heal.proposal is not None:
            return "proposal"
        if heal.abstention is not None:
            return "abstained"
    if target is None:
        return "-"
    if error_type == "TargetDrifted" and target.resolved_rank is not None:
        return f"drifted [{target.resolved_rank}]"
    if error_type == "AmbiguousTarget":
        return "ambiguous"
    if error_type == "TargetNotFound" and target.resolved_rank is None:
        return "not found"
    if target.resolved_rank is not None:
        return f"[{target.resolved_rank}] {target.selectors[target.resolved_rank].strategy}"
    return "-"


def _outcome(run: Run, run_directory: Path) -> list[str]:
    usage = model_usage_line(run.model_usage)
    return _status_lines(run, run_directory) + ([usage] if usage is not None else [])


def _status_lines(run: Run, run_directory: Path) -> list[str]:
    directory = run_directory / run.run_id
    total = len(run.steps)
    succeeded = sum(1 for step in run.steps if step.status is StepStatus.SUCCEEDED)
    seconds = f"{(run.duration_ms or 0) / 1000:.2f}s"
    if run.status is RunStatus.SUCCEEDED:
        lines = [f"SUCCEEDED · {succeeded}/{total} steps in {seconds}"]
        lines.extend(
            f"Download: {directory / step.artifacts.download}"
            for step in run.steps
            if step.artifacts.download is not None
        )
        return lines
    failed = run.failed_step
    error = run.error
    if failed is None:
        reason = f"{error.type}: {error.message}" if error is not None else "the run did not finish"
        return [
            f"FAILED before any step could finish: {reason} · {succeeded}/{total} steps succeeded"
        ]
    stopped = _STOPPED_WORDS.get(run.status, "FAILED")
    if error is not None and error.type == "HealAbstained":
        stopped = "ABSTAINED"
    lines = [
        f"{stopped} at step {failed.index + 1} {failed.step_id}: "
        f"{error.type if error is not None else 'error'} · "
        f"{succeeded}/{total} steps succeeded in {seconds}"
    ]
    evidence = [
        str(directory / name)
        for name in (
            failed.artifacts.screenshot,
            failed.artifacts.dom_snapshot,
            failed.artifacts.trace,
        )
        if name is not None
    ]
    if failed.artifacts.trace_withheld is not None:
        evidence.append(trace_withheld_line(failed.artifacts.trace_withheld))
    evidence.extend(f"not captured: {problem}" for problem in failed.artifacts.capture_errors)
    for position, item in enumerate(evidence):
        lines.append(("Evidence: " if position == 0 else "          ") + item)
    return lines


def trace_withheld_line(withheld: TraceWithheld) -> str:
    """Why a failure's trace was not kept, without implying the secret itself leaked."""
    if withheld.reason is TraceWithheldReason.SECRET_DETECTED:
        return (
            "trace withheld: the recorded trace contained a value typed from a secret, so it "
            "was deleted. Re-run with --headed to watch the failure live."
        )
    if withheld.typed_at_index is not None and withheld.typed_at_step is not None:
        where = f" at step {withheld.typed_at_index + 1} ({withheld.typed_at_step})"
    else:
        where = ""
    return (
        f"trace withheld: the failing page still held a value typed{where}, so the trace "
        "could contain it. Re-run with --headed to watch the failure live."
    )
