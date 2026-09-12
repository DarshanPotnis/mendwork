"""Readable run output: progress as events arrive, then a summary table.

Presentation only: every fact shown comes from the run's events and record.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from pydantic import JsonValue

from mendwork.engine.domain.enums import ActionType, ValueKind
from mendwork.engine.domain.events import (
    ActionPerformedEvent,
    CheckpointFailedEvent,
    CheckpointPassedEvent,
    RunEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFailedEvent,
    StepStartedEvent,
    StepSucceededEvent,
    TargetResolvedEvent,
)
from mendwork.engine.domain.runs import (
    ErrorReport,
    Run,
    RunStatus,
    SelectorOutcome,
    StepResult,
    StepStatus,
    TargetEvidence,
    TraceWithheld,
    TraceWithheldReason,
)

INDENT = "      "
_DIFFERENCE_WORDS = {
    "role": "role",
    "tag": "tag",
    "input_type": "type",
    "accessible_name": "accessible name",
    "unconfirmed": "identity not confirmed by Playwright",
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
    """One line on how Rung 0 found a target."""
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
    lines = [f"{INDENT}FAILED {error.type}: {error.message}"]
    context = error.context
    recorded = _identity(context.get("recorded"))
    found = _identity(context.get("found"))
    if recorded is not None and found is not None:
        lines.append(f"{INDENT}  recorded  {recorded}")
        lines.append(f"{INDENT}  found     {found}")
        differences = context.get("differences")
        if isinstance(differences, list) and differences:
            words = ", ".join(_DIFFERENCE_WORDS.get(str(item), str(item)) for item in differences)
            lines.append(f"{INDENT}  differs   {words}")
    if target is not None and error.type in {"AmbiguousTarget", "TargetNotFound"}:
        lines.extend(_selector_lines(target))
    if action_performed:
        lines.append(f"{INDENT}The action was performed before the step failed.")
    else:
        lines.append(f"{INDENT}No action was performed on this step.")
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
    result = "succeeded" if step.status is StepStatus.SUCCEEDED else "FAILED"
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
    lines = [
        f"FAILED at step {failed.index + 1} {failed.step_id}: "
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
