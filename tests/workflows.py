"""Builders for workflow documents and versions in tests.

Documents are plain JSON-like data, exactly what a decoder hands the engine, so tests can
break them in any way a person editing a file might.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.documents import parse_workflow_document
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import WorkflowValidationError

# Any: a document under construction is arbitrary JSON-like data by definition, and tests
# deliberately build ill-typed ones.
Document = dict[str, Any]

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLES_DIR: Final = REPO_ROOT / "workflows" / "examples"
EXAMPLE_IDS: Final = ("download_report", "view_order_detail")
CREATED_AT: Final = "2026-09-11T08:30:00Z"
CREATED_AT_DATETIME: Final = datetime(2026, 9, 11, 8, 30, tzinfo=UTC)
PORTAL_URL: Final = "https://portal.example.test/sign-in"


def selector(value: str = "save") -> Document:
    return {"strategy": "test_id", "value": value}


def fingerprint(**overrides: object) -> Document:
    return {
        "tag": "button",
        "role": "button",
        "accessible_name": "Save",
        "structural_path": "main > form > button",
        "selectors": [selector()],
        **overrides,
    }


def field_fingerprint(**overrides: object) -> Document:
    field: Document = {
        "tag": "input",
        "role": "textbox",
        "accessible_name": "Full name",
        "label_text": "Full name",
        "attributes": {"id": "full-name", "type": "text"},
        "selectors": [{"strategy": "label", "value": "Full name"}],
    }
    return fingerprint(**{**field, **overrides})


def literal(value: str) -> Document:
    return {"kind": "literal", "value": value}


def input_ref(name: str) -> Document:
    return {"kind": "input", "name": name}


def secret_ref(name: str) -> Document:
    return {"kind": "secret", "name": name}


def navigate_step(step_id: str = "open_portal", **overrides: object) -> Document:
    return {
        "id": step_id,
        "intent": "Open the portal",
        "action": "navigate",
        "risk": "safe",
        "value": literal(PORTAL_URL),
        **overrides,
    }


def click_step(step_id: str = "save", **overrides: object) -> Document:
    return {
        "id": step_id,
        "intent": "Click the 'Save' button",
        "action": "click",
        "risk": "caution",
        "target": fingerprint(),
        **overrides,
    }


def fill_step(step_id: str = "fill_name", **overrides: object) -> Document:
    return {
        "id": step_id,
        "intent": "Fill the 'Full name' field",
        "action": "fill",
        "risk": "caution",
        "target": field_fingerprint(),
        "value": literal("Ada Lovelace"),
        **overrides,
    }


def document(**overrides: object) -> Document:
    return {
        "schema_version": 1,
        "workflow_id": "demo",
        "version": 1,
        "created_at": CREATED_AT,
        "steps": [navigate_step(), fill_step(), click_step()],
        **overrides,
    }


def version(**overrides: object) -> WorkflowVersion:
    return parse_workflow_document(document(**overrides))


def problems(broken: Document) -> list[tuple[str, str]]:
    """The (path, message) of every issue a document is rejected with."""
    try:
        parse_workflow_document(broken)
    except WorkflowValidationError as error:
        return [(issue.path, issue.message) for issue in error.issues]
    raise AssertionError("the document was accepted")


def example_path(workflow_id: str) -> Path:
    return EXAMPLES_DIR / f"{workflow_id}.yaml"


def load_example(workflow_id: str) -> WorkflowVersion:
    path = example_path(workflow_id)
    return WorkflowYamlCodec(max_bytes=1 << 20).decode(path.read_bytes(), source=str(path))
