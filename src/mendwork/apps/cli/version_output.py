"""Readable output for workflow versions: history, diffs, imports, and rollbacks (ADR 0013).

Presentation only. The words for elements, checks, and why a version exists come from
``engine.patching.words``, which the run report shares, so a version reads the same everywhere.
"""

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final

from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.enums import PromotionPolicy, VerificationStrength
from mendwork.engine.domain.patches import PendingPatch
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.diff import FieldChange, StepDiff, VersionDiff
from mendwork.engine.patching.history import (
    PendingLine,
    history_entries,
    pending_lines,
    step_strengths,
    weak_summary,
)
from mendwork.engine.patching.manual_versions import ImportPlan, RollbackPlan
from mendwork.engine.patching.words import change_summary, heal_reason, moment, quoted

INDENT: Final = "  "
_STRENGTH_WORDS: Final[Mapping[VerificationStrength, str]] = {
    VerificationStrength.STRONG: "strong",
    VerificationStrength.WEAK: "weak",
    VerificationStrength.NONE: "not verified",
}


def history_text(
    versions: Sequence[WorkflowVersion],
    pending: Sequence[PendingPatch],
    *,
    promotion: PromotionPolicy,
    required: int,
    store: Path,
) -> str:
    """A workflow's versions newest first, the latest version's checks, and its pending patches."""
    latest = versions[-1]
    lines = [f"{latest.workflow_id} · {_count(len(versions), 'version')} in {store}"]
    lines.extend(
        table(
            (
                f"v{entry.version}",
                moment(entry.created_at),
                entry.summary if entry.run_id is None else f"{entry.summary} · run {entry.run_id}",
            )
            for entry in history_entries(versions)
        )
    )
    strengths = step_strengths(latest)
    lines.extend(["", f"Checks in v{latest.version}:"])
    lines.extend(
        table(
            (
                str(item.index + 1),
                item.step_id,
                ", ".join(item.checks) or "no checks",
                _STRENGTH_WORDS[item.strength],
            )
            for item in strengths
        )
    )
    warning = weak_summary(strengths)
    if warning is not None:
        lines.append(warning)
    waiting = pending_lines(latest, pending)
    after_n = promotion is PromotionPolicy.AFTER_N_SUCCESSES
    if waiting or after_n:
        heading = (
            f"Pending patches, each saved as a version once {required} succeeded runs verify it:"
            if after_n
            else "Pending patches, not tried while MENDWORK_PATCH_PROMOTION is immediate:"
        )
        lines.extend(["", heading])
        lines.extend(pending_line(line, required) for line in waiting)
        if not waiting:
            lines.append(f"{INDENT}none")
    return "\n".join(lines)


def pending_line(line: PendingLine, required: int | None = None) -> str:
    """One pending patch: its step, the element it targets, and the runs that verified it."""
    count = (
        f"{line.successes} of {required} runs"
        if required is not None
        else _count(line.successes, "run")
    )
    return (
        f"{INDENT}Step {line.index + 1} {line.step_id}: {line.target}, verified in {count} "
        f"({', '.join(line.runs)})"
    )


def diff_text(diff: VersionDiff) -> str:
    """Everything that differs between two versions, step by step, with why a heal was made."""
    before, after = f"v{diff.before.version}", f"v{diff.after.version}"
    lines = [
        f"{diff.workflow_id} {before} → {after} · {_count(len(diff.steps), 'step')} changed, "
        f"{diff.unchanged_steps} unchanged"
    ]
    for step in diff.steps:
        lines.extend(["", *step_lines(step, before, after, _reasons(diff, step.step_id))])
    lines.extend(_declaration_lines(diff.declarations))
    if not diff.steps and not diff.declarations:
        lines.append("Both versions have the same steps, inputs, and secrets.")
    if diff.between:
        lines.extend(["", f"Versions after {before}:"])
        lines.extend(
            table(
                (f"v{item.version}", change_summary(item.change, diff.after.steps))
                for item in diff.between
            )
        )
    return "\n".join(lines)


def step_lines(step: StepDiff, before: str, after: str, reasons: Sequence[str] = ()) -> list[str]:
    """One changed step: why, what changed about it, and its element on each side."""
    lines = [f"Step {step.index + 1} {step.step_id} · {quoted(step.intent)}"]
    lines.extend(
        f"{INDENT}{'Why: ' if position == 0 else '     '}{paragraph}"
        for position, paragraph in enumerate(reasons)
    )
    lines.extend(f"{INDENT}{_field_words(field)}" for field in step.fields)
    target = step.target
    if target is not None:
        rows = [("Element", before, after, "")]
        rows.extend(
            (field.label, field.before or "none", field.after or "none", _changed(field.changed))
            for field in target.fields
        )
        rows.extend(
            (
                "Found by" if position == 0 else "",
                line.before or "none",
                line.after or "none",
                _changed(line.before != line.after),
            )
            for position, line in enumerate(target.selectors)
        )
        lines.extend(table(rows))
    lines.extend(f"{INDENT}Check added: {words}" for words in step.checkpoints_added)
    lines.extend(f"{INDENT}Check removed: {words}" for words in step.checkpoints_removed)
    return lines


def import_text(plan: ImportPlan, path: str, store: Path) -> str:
    """What an import will do, for a person to confirm."""
    version, latest, diff = plan.version, plan.latest, plan.diff
    workflow_id = version.workflow_id
    if latest is None or diff is None:
        return (
            f"Importing {path} stores it as {workflow_id} v1, the first version of {workflow_id} "
            f"in {store}. Runs of the file then save their heals."
        )
    before, after = f"v{latest.version}", f"v{version.version}"
    lines = [
        f"Importing {path} creates {workflow_id} {after}, edited by hand from {before}, the latest "
        f"stored version ({change_summary(latest.change, latest.steps)})."
    ]
    if plan.same_as is not None:
        lines.append(
            f"The file has the content of v{plan.same_as}. mendwork rollback {workflow_id} --to "
            f"{plan.same_as} restores that as a rollback instead, which also keeps the heals it "
            "undoes from being saved again."
        )
    if diff.steps:
        lines.extend(["", f"Steps that differ from {before}:"])
        for position, step in enumerate(diff.steps):
            lines.extend([""] if position else [])
            lines.extend(step_lines(step, before, after))
    lines.extend(_declaration_lines(diff.declarations))
    lines.append("")
    if plan.stops_matching:
        lines.append("Pending patches on those steps will stop matching and never become versions:")
        lines.extend(pending_line(line) for line in plan.stops_matching)
    else:
        lines.append("No pending patches are affected.")
    lines.append(
        f"Afterwards, runs of this file run {after} and save their heals; a file with an earlier "
        "version's content runs as written and saves nothing."
    )
    return "\n".join(lines)


def rollback_text(plan: RollbackPlan) -> str:
    """What a rollback undoes, and how the steps change."""
    version, latest, restored = plan.version, plan.latest, plan.restored
    workflow_id = version.workflow_id
    before, after = f"v{latest.version}", f"v{version.version}"
    lines = [
        f"Rolling back {workflow_id} to v{restored.version} creates {after} with the content of "
        f"v{restored.version}. Nothing is deleted. It undoes:"
    ]
    lines.extend(
        table(
            (f"v{entry.version}", moment(entry.created_at), entry.summary) for entry in plan.undone
        )
    )
    if plan.undoes_heals:
        lines.append(
            "A heal it undoes is not saved again automatically, even when a later run verifies it."
        )
    if plan.diff.steps:
        lines.extend(["", f"Steps that change from {before}:"])
        for position, step in enumerate(plan.diff.steps):
            lines.extend([""] if position else [])
            lines.extend(step_lines(step, before, after))
    lines.extend(_declaration_lines(plan.diff.declarations))
    if plan.stops_matching:
        lines.extend(
            ["", "Pending patches on those steps stop matching and will never become versions:"]
        )
        lines.extend(pending_line(line) for line in plan.stops_matching)
    return "\n".join(lines)


def table(rows: Iterable[Sequence[str]]) -> list[str]:
    """Rows as indented columns, each as wide as its widest cell."""
    materialized = [tuple(row) for row in rows]
    if not materialized:
        return []
    widths = [
        max(len(row[column]) for row in materialized) for column in range(len(materialized[0]))
    ]
    return [
        (
            INDENT + "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        ).rstrip()
        for row in materialized
    ]


def _reasons(diff: VersionDiff, step_id: str) -> list[str]:
    step = next((item for item in diff.after.steps if item.id == step_id), None)
    reasons: list[str] = []
    for item in diff.between:
        change = item.change
        if isinstance(change, HealChange) and change.step_id == step_id:
            first, *rest = heal_reason(change, step)
            reasons.extend([f"v{item.version}: {first}", *rest])
    return reasons


def _declaration_lines(fields: Sequence[FieldChange]) -> list[str]:
    return ["", *(_field_words(field) for field in fields)] if fields else []


def _field_words(field: FieldChange) -> str:
    return f"{field.label}: {field.before or 'none'} → {field.after or 'none'}"


def _changed(changed: bool) -> str:
    return "changed" if changed else ""


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
