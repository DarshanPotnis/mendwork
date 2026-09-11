"""The JSON Schema for workflow files, generated from the domain models.

Editors use it for autocomplete and inline errors. It describes structure only; rules
that span fields (references, credentials, lineage) are enforced by the models, which
remain the single source of truth.
"""

import json
from typing import Final

from pydantic import JsonValue

from mendwork.engine.domain.workflow import WorkflowVersion

JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"


def workflow_json_schema() -> dict[str, JsonValue]:
    """The schema as data, with the dialect declared and a human title."""
    schema = WorkflowVersion.model_json_schema(mode="validation")
    return {"$schema": JSON_SCHEMA_DIALECT, **schema, "title": "Mendwork workflow version"}


def workflow_json_schema_text() -> str:
    """The schema exactly as committed to schemas/workflow.schema.json."""
    return json.dumps(workflow_json_schema(), indent=2, ensure_ascii=False) + "\n"
