"""Builders for heal change records, shared by the domain, patching, and CLI tests."""

from typing import Final

from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.run_identifiers import parse_run_id
from mendwork.engine.domain.steps import step_target
from mendwork.engine.domain.workflow import WorkflowVersion

HEAL_RUN: Final = parse_run_id("20260915T180610Z-c924bb3e")
APPROVAL: Final = {
    "proposal_id": "save-1",
    "audit_sequence": 3,
    "decided_at": "2026-09-15T18:00:00Z",
}


def target_of(version: WorkflowVersion, step_id: str) -> Fingerprint:
    """The target a step has in a version."""
    step = next(item for item in version.steps if item.id == step_id)
    target = step_target(step)
    assert target is not None, f"step {step_id} has no target"
    return target


def renamed_target(target: Fingerprint, name: str) -> Fingerprint:
    """The same target, with another accessible name."""
    return Fingerprint.model_validate({**target.model_dump(mode="json"), "accessible_name": name})


def heal_change(
    version: WorkflowVersion,
    step_id: str = "save",
    *,
    new_target: Fingerprint | None = None,
    **overrides: object,
) -> HealChange:
    """A verified Rung 2 heal of a step in a version, with its evidence."""
    old = target_of(version, step_id)
    values: dict[str, object] = {
        "kind": "heal",
        "step_id": step_id,
        "rung": 2,
        "old_target": old.model_dump(mode="json"),
        "new_target": (new_target or renamed_target(old, "Save changes")).model_dump(mode="json"),
        "checkpoints": ["text_present"],
        "strength": "strong",
        "score": 0.85,
        "margin": 0.63,
        "threshold": 0.6,
        "required_margin": 0.15,
        "evidence": {
            "run_id": HEAL_RUN,
            "report": "report.html",
            "step_screenshot": f"steps/003_{step_id}.png",
            "found_screenshot": f"steps/003_{step_id}.found.png",
        },
        "promotion": {"policy": "immediate", "runs": [HEAL_RUN]},
        **overrides,
    }
    return HealChange.model_validate(values)
