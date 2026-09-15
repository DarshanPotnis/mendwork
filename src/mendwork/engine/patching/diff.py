"""What changed between two versions of a workflow, step by step (ADR 0013).

A diff compares steps by their ids, which never change between versions. For a step whose target
changed it lists what the element is (kind, name, identifiers, nearby text, place in the page) and
how Mendwork finds it, each selector paired with the one of the same strategy on the other side, so
a reader sees "was a button named …, now a link named …" rather than raw YAML. The words come from
``words``; this module decides what is compared.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from mendwork.engine.domain.changes import ChangeRecord
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.steps import PressStep, Step, step_target, step_value
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.words import (
    checkpoint_words,
    fingerprint_kind,
    quoted,
    selector_words,
    value_words,
)

LEVEL_SEPARATOR: Final = f" {chr(0x203A)} "
"""Between the levels of a place in the page: a right-pointing angle quotation mark."""


@dataclass(frozen=True, slots=True)
class FieldChange:
    """One compared property: its words before and after, and whether it changed."""

    label: str
    before: str | None
    after: str | None
    changed: bool


@dataclass(frozen=True, slots=True)
class SelectorLine:
    """One way of finding the element, before and after; None where there was none."""

    before: str | None
    after: str | None


@dataclass(frozen=True, slots=True)
class TargetDiff:
    """How a step's element changed."""

    fields: tuple[FieldChange, ...]
    """Every property either side has, changed or not."""
    selectors: tuple[SelectorLine, ...]
    """The new selectors best first, then those that were removed."""


@dataclass(frozen=True, slots=True)
class StepDiff:
    """One step that differs between the two versions."""

    index: int
    step_id: str
    intent: str
    """The intent in the later version."""
    fields: tuple[FieldChange, ...]
    """Intent, risk, value, and key, where they changed."""
    target: TargetDiff | None
    checkpoints_added: tuple[str, ...]
    checkpoints_removed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """A version between the two compared, and why it exists."""

    version: int
    change: ChangeRecord | None


@dataclass(frozen=True, slots=True)
class VersionDiff:
    """Everything that differs between two versions of one workflow."""

    workflow_id: str
    before: WorkflowVersion
    after: WorkflowVersion
    steps: tuple[StepDiff, ...]
    unchanged_steps: int
    declarations: tuple[FieldChange, ...]
    """Inputs and secrets declared, where they changed."""
    between: tuple[HistoryItem, ...]
    """The versions after ``before`` up to ``after``, when ``after`` is the later one."""


_ATTRIBUTE_FIELDS: Final[tuple[tuple[str, Callable[[Fingerprint], str | None]], ...]] = (
    ("Test id", lambda item: item.attributes.data_testid),
    ("Id on the page", lambda item: item.attributes.id),
    ("Name attribute", lambda item: item.attributes.name),
    ("Autocomplete", lambda item: item.attributes.autocomplete),
    ("Placeholder", lambda item: item.attributes.placeholder),
    ("Aria label", lambda item: item.attributes.aria_label),
    ("Link address", lambda item: item.attributes.href),
)


def diff_versions(
    before: WorkflowVersion, after: WorkflowVersion, history: Sequence[WorkflowVersion] = ()
) -> VersionDiff:
    """What changed from ``before`` to ``after``; ``history`` supplies the versions in between."""
    changed: list[StepDiff] = []
    for index, (old, new) in enumerate(zip(before.steps, after.steps, strict=True)):
        if old != new:
            changed.append(step_diff(index, old, new))
    between = tuple(
        HistoryItem(version=item.version, change=item.change)
        for item in history
        if before.version < item.version <= after.version
    )
    return VersionDiff(
        workflow_id=after.workflow_id,
        before=before,
        after=after,
        steps=tuple(changed),
        unchanged_steps=len(after.steps) - len(changed),
        declarations=_declarations(before, after),
        between=between,
    )


def step_diff(index: int, old: Step, new: Step) -> StepDiff:
    """How one step changed."""
    fields = [
        _field("Intent", quoted(old.intent), quoted(new.intent)),
        _field("Risk", old.risk.value, new.risk.value),
        _field("Value", value_words(step_value(old)), value_words(step_value(new))),
    ]
    old_value, new_value = step_value(old), step_value(new)
    if old_value != new_value and fields[2].before == fields[2].after:
        fields[2] = FieldChange("Value", fields[2].before, fields[2].after, changed=True)
    if isinstance(old, PressStep) and isinstance(new, PressStep):
        fields.append(_field("Key", old.key, new.key))
    old_target, new_target = step_target(old), step_target(new)
    target = (
        target_diff(old_target, new_target)
        if old_target is not None and new_target is not None and old_target != new_target
        else None
    )
    return StepDiff(
        index=index,
        step_id=new.id,
        intent=new.intent,
        fields=tuple(field for field in fields if field.changed),
        target=target,
        checkpoints_added=tuple(
            checkpoint_words(item) for item in new.checkpoints if item not in old.checkpoints
        ),
        checkpoints_removed=tuple(
            checkpoint_words(item) for item in old.checkpoints if item not in new.checkpoints
        ),
    )


def target_diff(old: Fingerprint, new: Fingerprint) -> TargetDiff:
    """How a step's element changed: what it is, and how it is found."""
    type_label = (
        "Button type"
        if "button" in (old.tag, new.tag)
        else "Field type"
        if "input" in (old.tag, new.tag)
        else "Type"
    )
    candidates: list[tuple[str, str | None, str | None]] = [
        ("Kind", fingerprint_kind(old), fingerprint_kind(new)),
        ("Name", _quoted(old.accessible_name), _quoted(new.accessible_name)),
        ("Text", _quoted(old.text), _quoted(new.text)),
        ("Label", _quoted(old.label_text), _quoted(new.label_text)),
        *((label, _quoted(read(old)), _quoted(read(new))) for label, read in _ATTRIBUTE_FIELDS),
        (type_label, _quoted(old.attributes.type), _quoted(new.attributes.type)),
        ("Nearby text", _texts(old.nearby_text), _texts(new.nearby_text)),
        ("Place in the page", place_words(old.structural_path), place_words(new.structural_path)),
    ]
    fields = tuple(
        _field(label, before, after)
        for label, before, after in candidates
        if before is not None or after is not None
    )
    return TargetDiff(fields=fields, selectors=_selector_lines(old, new))


def place_words(structural_path: str) -> str:
    """A structural path as levels a person reads, outermost first."""
    return structural_path.replace(" > ", LEVEL_SEPARATOR)


def _selector_lines(old: Fingerprint, new: Fingerprint) -> tuple[SelectorLine, ...]:
    remaining = list(old.selectors)
    lines: list[SelectorLine] = []
    for selector in new.selectors:
        same = next((item for item in remaining if item == selector), None)
        paired = same or next(
            (item for item in remaining if item.strategy == selector.strategy), None
        )
        if paired is not None:
            remaining.remove(paired)
        before = selector_words(paired) if paired is not None else None
        lines.append(SelectorLine(before=before, after=selector_words(selector)))
    lines.extend(SelectorLine(before=selector_words(item), after=None) for item in remaining)
    return tuple(lines)


def _declarations(before: WorkflowVersion, after: WorkflowVersion) -> tuple[FieldChange, ...]:
    old_inputs = ", ".join(sorted(item.name for item in before.inputs)) or None
    new_inputs = ", ".join(sorted(item.name for item in after.inputs)) or None
    old_secrets = ", ".join(sorted(before.secrets)) or None
    new_secrets = ", ".join(sorted(after.secrets)) or None
    fields = (
        _field("Inputs", old_inputs, new_inputs),
        _field("Secrets", old_secrets, new_secrets),
    )
    return tuple(field for field in fields if field.changed)


def _field(label: str, before: str | None, after: str | None) -> FieldChange:
    return FieldChange(label=label, before=before, after=after, changed=before != after)


def _quoted(text: str | None) -> str | None:
    return quoted(text) if text else None


def _texts(texts: Sequence[str]) -> str | None:
    return ", ".join(quoted(text) for text in texts) or None
