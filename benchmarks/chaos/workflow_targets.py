"""Which chaos-portal target each workflow step acts on: benchmark ground truth.

The mapping lives beside the benchmark, not in the workflow format, so workflows stay
portal-agnostic. One JSON file per example workflow, named after its id.
"""

import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

WORKFLOW_TARGETS_DIR: Final = Path(__file__).resolve().parent / "workflow_targets"


class WorkflowTargets(BaseModel):
    """Step id to chaos target key, for every step of one workflow that has a target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    targets: dict[str, str]


def load_workflow_targets(
    workflow_id: str, directory: Path = WORKFLOW_TARGETS_DIR
) -> WorkflowTargets:
    """The mapping for one workflow, validated."""
    path = directory / f"{workflow_id}.json"
    mapping = WorkflowTargets.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if mapping.workflow_id != workflow_id:
        raise ValueError(f"{path} maps {mapping.workflow_id}, not {workflow_id}")
    return mapping
