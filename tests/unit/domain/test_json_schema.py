"""The committed JSON Schema is fresh and agrees with the models about the examples."""

import json

import pytest
from jsonschema import Draft202012Validator

from mendwork.engine.domain.documents import workflow_document
from mendwork.engine.domain.json_schema import workflow_json_schema, workflow_json_schema_text
from tests.workflows import EXAMPLE_IDS, REPO_ROOT, example_path, load_example

SCHEMA_PATH = REPO_ROOT / "schemas" / "workflow.schema.json"


def test_the_committed_schema_is_up_to_date() -> None:
    committed = SCHEMA_PATH.read_text(encoding="utf-8")

    assert committed == workflow_json_schema_text(), (
        "schemas/workflow.schema.json is stale; regenerate it with `make schema` and commit it"
    )


def test_the_schema_is_a_valid_draft_2020_12_schema() -> None:
    Draft202012Validator.check_schema(workflow_json_schema())


@pytest.mark.parametrize("workflow_id", EXAMPLE_IDS)
def test_examples_validate_against_the_schema_as_written_and_as_canonical(workflow_id: str) -> None:
    import yaml

    validator = Draft202012Validator(workflow_json_schema())
    as_written = yaml.safe_load(example_path(workflow_id).read_text(encoding="utf-8"))
    canonical = json.loads(json.dumps(workflow_document(load_example(workflow_id))))

    assert list(validator.iter_errors(as_written)) == []
    assert list(validator.iter_errors(canonical)) == []


def test_the_schema_rejects_what_the_models_reject_structurally() -> None:
    validator = Draft202012Validator(workflow_json_schema())
    broken = json.loads(json.dumps(workflow_document(load_example("download_report"))))
    broken["steps"][0]["target"] = {"tag": "a"}

    assert list(validator.iter_errors(broken))
