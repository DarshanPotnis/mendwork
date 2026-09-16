"""Which chaos-portal target each workflow step acts on, and how to run it: benchmark ground truth.

The mapping lives beside the benchmark, not in the workflow format, so workflows stay
portal-agnostic. One JSON file per example workflow, named after its id. ``inputs`` are templates
for the run's inputs, with ``{portal}``, ``{seed}``, and ``{level}`` filled in per run; ``secrets``
are the portal's fictional, documented demo credentials.
"""

import json
import string
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

WORKFLOW_TARGETS_DIR: Final = Path(__file__).resolve().parent / "workflow_targets"
TEMPLATE_FIELDS: Final = frozenset({"portal", "seed", "level"})


class WorkflowTargets(BaseModel):
    """Step id to chaos target key for every step with a target, and the run's inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str
    targets: dict[str, str]
    inputs: dict[str, str] = {}
    secrets: dict[str, str] = {}

    def run_inputs(self, *, portal: str, seed: int, level: int) -> dict[str, str]:
        """The inputs for one run on the portal at ``portal``, a seed, and a level."""
        values = {"portal": portal, "seed": str(seed), "level": str(level)}
        return {name: template.format_map(values) for name, template in self.inputs.items()}


def template_fields(template: str) -> set[str]:
    """The placeholders a template uses."""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}


def load_workflow_targets(
    workflow_id: str, directory: Path = WORKFLOW_TARGETS_DIR
) -> WorkflowTargets:
    """The mapping for one workflow, validated; unknown placeholders are refused."""
    path = directory / f"{workflow_id}.json"
    mapping = WorkflowTargets.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if mapping.workflow_id != workflow_id:
        raise ValueError(f"{path} maps {mapping.workflow_id}, not {workflow_id}")
    for name, template in mapping.inputs.items():
        unknown = template_fields(template) - TEMPLATE_FIELDS
        if unknown:
            raise ValueError(f"{path}: input {name} uses unknown placeholders {sorted(unknown)}")
    return mapping
