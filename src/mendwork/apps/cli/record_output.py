"""Readable recording output: a line per event while recording, then the step summary.

Presentation only. Fill values are never printed: a fill is shown as "with a typed value"
or "with a secret", and naming prompts describe the field, not what was typed into it.
"""

from collections.abc import Iterable
from typing import Final, TextIO

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.recording import (
    DraftStep,
    DropReason,
    IgnoredReason,
    InteractionIgnored,
    LiteralDraft,
    NavigationIgnored,
    Recording,
    RecordingNotice,
    SecretDraft,
    StepRecorded,
)
from mendwork.engine.domain.selectors import ByCss
from mendwork.engine.recording.naming import Decisions

IGNORED_WORDS: Final = {
    IgnoredReason.MODIFIED_CLICK: (
        "a click with a modifier key or another mouse button: nothing was recorded; "
        "repeat it with a plain click"
    ),
    IgnoredReason.MODIFIED_KEY: (
        "a key pressed with a modifier: nothing was recorded; repeat it without the modifier"
    ),
    IgnoredReason.DOUBLE_CLICK: (
        "a double-click: only single clicks are recorded; if the action is still needed, "
        "repeat it with one plain click"
    ),
    IgnoredReason.FILE_INPUT: (
        "a click on a file upload: uploads cannot be recorded yet; nothing was recorded"
    ),
    IgnoredReason.FRAME: (
        "an interaction inside a frame: frames cannot be recorded yet; nothing was recorded"
    ),
    IgnoredReason.BUSY: (
        "an interaction made while a step was still being recorded: nothing was recorded; "
        "wait for its 'recorded' line, then repeat it with a plain click"
    ),
    IgnoredReason.NOT_ACTIONABLE: (
        "a click on a control that was hidden, disabled, or covered: it did nothing and "
        "nothing was recorded; repeat it with a plain click once the control is ready"
    ),
}
_DROP_WORDS: Final = {
    DropReason.NO_MATCH: "matched nothing",
    DropReason.DIFFERENT_ELEMENT: "found a different element",
    DropReason.AMBIGUOUS: "matched several elements, even when scoped",
}


class HumanRecordingProgress:
    """A RecordingObserver that prints each step and each ignored interaction as it happens."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    async def notify(self, notice: RecordingNotice) -> None:
        self._stream.write(notice_line(notice) + "\n")
        self._stream.flush()


def notice_line(notice: RecordingNotice) -> str:
    """One line of live recording output."""
    match notice:
        case StepRecorded():
            verb = "updated " if notice.replaced else "recorded"
            return f"  {verb}  {step_line(notice.step)}"
        case InteractionIgnored():
            return f"  ignored   {IGNORED_WORDS[notice.reason]}"
        case NavigationIgnored():
            return f"  noted     the page went to {notice.url} by itself; that is not a step"


def step_line(step: DraftStep, decisions: Decisions | None = None) -> str:
    """ "<n>. <ACTION> <target>", the line a person checks to spot a wrong step."""
    return f"{step.index + 1}. {step.description}{_value_words(step, decisions)}"


def render_recording_summary(recording: Recording, decisions: Decisions | None = None) -> str:
    """Every step, its detail, and every warning."""
    count = len(recording.steps)
    ignored = recording.ignored_count
    lines = [
        "",
        f"Recorded {count} step{'s' if count != 1 else ''} · "
        f"{ignored} interaction{'s' if ignored != 1 else ''} ignored",
        "",
    ]
    for step in recording.steps:
        lines.append(f" {step_line(step, decisions)}")
        lines.append(f"      {_detail(step)}")
    warnings = recording_warnings(recording)
    if warnings:
        lines.extend(["", "Warnings"])
        lines.extend(f"  - {warning}" for warning in warnings)
    return "\n".join(lines)


def recording_warnings(recording: Recording) -> list[str]:
    """What a reviewer should look at before trusting the recording."""
    warnings: list[str] = []
    for step in recording.steps:
        where = f"{step.index + 1}. {step.step_id}"
        if not step.checkpoints:
            warnings.append(
                f"{where}: no checkpoint passed when it was recorded, so a replay cannot tell "
                "whether this step worked"
            )
        if step.target is not None:
            selectors = step.target.selectors
            if len(selectors) == 1 or all(isinstance(selector, ByCss) for selector in selectors):
                strategies = ", ".join(selector.strategy for selector in selectors)
                warnings.append(
                    f"{where}: low selector confidence: only {len(selectors)} selector"
                    f"{'s' if len(selectors) != 1 else ''} survived ({strategies})"
                )
        warnings.extend(
            f"{where}: dropped selector {dropped.summary}: {_DROP_WORDS[dropped.reason]} "
            f"({'/'.join(str(count) for count in dropped.level_counts) or '0'})"
            for dropped in step.dropped_selectors
        )
        warnings.extend(
            f"{where}: dropped {dropped.kind} checkpoint: {dropped.reason}"
            for dropped in step.dropped_checkpoints
        )
    warnings.extend(
        f"the page went to {notice.url} by itself; that is not a step"
        for notice in recording.notices
        if isinstance(notice, NavigationIgnored)
    )
    return warnings


def written_line(path: str, steps: int, decisions: Decisions) -> str:
    """What was written, and which inputs and secrets a run needs."""
    return (
        f"Wrote {path}: {steps} steps · inputs: {_names(d.name for d in decisions.inputs)} · "
        f"secrets: {_names(d.name for d in decisions.secrets)}"
    )


def _detail(step: DraftStep) -> str:
    parts: list[str] = [step.step_id]
    if step.selector is not None:
        parts.append(
            f"selector {step.selector.rank + 1} of {step.selector.total} ({step.selector.strategy})"
        )
    parts.append(f"risk {step.risk}")
    checkpoints = ", ".join(checkpoint.kind for checkpoint in step.checkpoints) or "none"
    parts.append(f"checkpoints: {checkpoints}")
    return " · ".join(parts)


def _value_words(step: DraftStep, decisions: Decisions | None) -> str:
    if step.action is not ActionType.FILL:
        return ""
    if isinstance(step.value, SecretDraft):
        secret = next(
            (d.name for d in (decisions.secrets if decisions else ()) if d.step == step.index),
            None,
        )
        return f" with secret {secret}" if secret else " with a secret"
    if isinstance(step.value, LiteralDraft) and decisions is not None:
        run_input = next((d.name for d in decisions.inputs if step.index in d.steps), None)
        if run_input is not None:
            return f" with input {run_input}"
    return " with a typed value"


def _names(names: Iterable[str]) -> str:
    listed = list(dict.fromkeys(names))
    return ", ".join(listed) if listed else "none"
