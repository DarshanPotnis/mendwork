"""A short, valid lineage for store tests."""

from datetime import timedelta

from mendwork.engine.domain.lineage import edit_version
from mendwork.engine.domain.steps import ClickStep
from mendwork.engine.domain.workflow import WorkflowContent, WorkflowVersion
from tests.fakes.clock import FakeClock
from tests.workflows import CREATED_AT_DATETIME, version


def child_of(parent: WorkflowVersion, intent: str, *, minutes: int = 1) -> WorkflowVersion:
    steps = tuple(
        step.model_copy(update={"intent": intent}) if isinstance(step, ClickStep) else step
        for step in parent.steps
    )
    clock = FakeClock(CREATED_AT_DATETIME + timedelta(minutes=minutes))
    content = WorkflowContent(inputs=parent.inputs, secrets=parent.secrets, steps=steps)
    return edit_version(parent, content, summary=f"Renamed to {intent}", clock=clock)


def lineage(length: int) -> list[WorkflowVersion]:
    versions = [version()]
    for number in range(2, length + 1):
        versions.append(child_of(versions[-1], f"Click save, revision {number}", minutes=number))
    return versions
