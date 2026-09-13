"""Turning a recording and its naming decisions into version 1 of a workflow.

The document is built and then validated by the same strict parser that reads workflow
files, so a recording can never produce a workflow Mendwork would refuse to load.
"""

from collections.abc import Mapping
from datetime import UTC

from pydantic import JsonValue

from mendwork.engine.domain.documents import parse_workflow_document
from mendwork.engine.domain.enums import ActionType, ValueKind
from mendwork.engine.domain.recording import DraftStep, LiteralDraft, Recording, SecretDraft
from mendwork.engine.domain.workflow import CURRENT_SCHEMA_VERSION, WorkflowVersion
from mendwork.engine.errors import WorkflowValidationError
from mendwork.engine.ports.clock import Clock
from mendwork.engine.recording.failures import UnusableReason, unusable
from mendwork.engine.recording.naming import Decisions, InputDecision


def _declaration(decision: InputDecision) -> JsonValue:
    declaration: dict[str, JsonValue] = {"name": decision.name, "kind": decision.kind.value}
    if decision.description is not None:
        declaration["description"] = decision.description
    return declaration


def assemble(
    recording: Recording, decisions: Decisions, *, workflow_id: str, clock: Clock
) -> WorkflowVersion:
    """Version 1 of the recorded workflow. Raises RecordingUnusable if it would be invalid."""
    inputs = {step: decision.name for decision in decisions.inputs for step in decision.steps}
    secrets = {decision.step: decision.name for decision in decisions.secrets}
    declared_secrets: list[str] = []
    for decision in decisions.secrets:
        if decision.name not in declared_secrets:
            declared_secrets.append(decision.name)
    created = clock.now().astimezone(UTC).replace(microsecond=0)
    document: dict[str, JsonValue] = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "version": 1,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "inputs": [_declaration(decision) for decision in decisions.inputs],
        "secrets": list(declared_secrets),
        "steps": [_step(step, inputs, secrets) for step in recording.steps],
    }
    try:
        return parse_workflow_document(document)
    except WorkflowValidationError as error:
        raise unusable(
            UnusableReason.INVALID_WORKFLOW,
            "the recorded steps do not form a valid workflow",
            problems=[f"{issue.path}: {issue.message}" for issue in error.issues],
        ) from error


def _step(step: DraftStep, inputs: Mapping[int, str], secrets: Mapping[int, str]) -> JsonValue:
    document: dict[str, JsonValue] = {
        "id": step.step_id,
        "intent": step.intent,
        "action": step.action.value,
        "risk": step.risk.value,
    }
    if step.target is not None:
        document["target"] = step.target.model_dump(mode="json", exclude_defaults=True)
    if step.action in {ActionType.NAVIGATE, ActionType.FILL, ActionType.SELECT}:
        document["value"] = _value(step, inputs, secrets)
    if step.key is not None:
        document["key"] = step.key
    document["checkpoints"] = [
        checkpoint.model_dump(mode="json", exclude_defaults=True) for checkpoint in step.checkpoints
    ]
    return document


def _value(step: DraftStep, inputs: Mapping[int, str], secrets: Mapping[int, str]) -> JsonValue:
    if isinstance(step.value, SecretDraft):
        name = secrets.get(step.index, step.value.proposed_name)
        return {"kind": ValueKind.SECRET.value, "name": name}
    if step.index in inputs:
        return {"kind": ValueKind.INPUT.value, "name": inputs[step.index]}
    literal = step.value.value if isinstance(step.value, LiteralDraft) else ""
    return {"kind": ValueKind.LITERAL.value, "value": literal}
