"""Documents: the schema version gate, strict typing, and canonical output."""

from typing import Any

import pytest
from pydantic import JsonValue

from mendwork.engine.domain.documents import (
    SUPPORTED_SCHEMA_VERSIONS,
    parse_workflow_document,
    workflow_document,
)
from mendwork.engine.domain.steps import step_target
from mendwork.engine.errors import UnsupportedSchemaVersion, WorkflowValidationError
from tests.workflows import click_step, document, fingerprint, problems, version


def test_the_supported_schema_version_is_one() -> None:
    assert SUPPORTED_SCHEMA_VERSIONS == (1,)


def test_an_unsupported_schema_version_is_one_clear_error() -> None:
    newer = document(schema_version=2, future_field="ignored until supported")

    with pytest.raises(UnsupportedSchemaVersion) as caught:
        parse_workflow_document(newer)

    assert caught.value.context["found"] == 2
    assert caught.value.context["supported"] == (1,)
    assert [(issue.path, issue.message) for issue in caught.value.issues] == [
        ("schema_version", "schema version 2 is not supported; this Mendwork reads version 1")
    ]


@pytest.mark.parametrize(
    ("raw", "path", "message"),
    [
        ([], "", "a workflow file must be a mapping of fields, starting with 'schema_version: 1'"),
        ({"workflow_id": "demo"}, "schema_version", "is required; this Mendwork reads version 1"),
        ({"schema_version": "1"}, "schema_version", "must be a whole number, such as 1"),
        ({"schema_version": True}, "schema_version", "must be a whole number, such as 1"),
    ],
)
def test_a_missing_or_malformed_schema_version_is_explained(
    raw: JsonValue, path: str, message: str
) -> None:
    with pytest.raises(WorkflowValidationError) as caught:
        parse_workflow_document(raw)

    assert not isinstance(caught.value, UnsupportedSchemaVersion)
    assert [(issue.path, issue.message) for issue in caught.value.issues] == [(path, message)]


@pytest.mark.parametrize(
    ("overrides", "path", "message"),
    [
        ({"version": "1"}, "version", "must be a whole number"),
        ({"version": 1.0}, "version", "must be a whole number"),
        (
            {"workflow_id": 42},
            "workflow_id",
            "must be text; YAML read this as a number, so put the value in quotes",
        ),
        (
            {"workflow_id": False},
            "workflow_id",
            "must be text; YAML read this as true/false, so put the value in quotes",
        ),
        ({"steps": "all of them"}, "steps", "must be a list"),
        ({"secrets": [None]}, "secrets[0]", "must be text"),
    ],
)
def test_scalar_types_are_strict(overrides: dict[str, Any], path: str, message: str) -> None:
    assert problems(document(**overrides)) == [(path, message)]


def test_strict_booleans_reject_truthy_strings() -> None:
    selector = {"strategy": "text", "value": "Save", "exact": "yes"}
    broken = document(steps=[click_step(target=fingerprint(selectors=[selector]))])

    assert problems(broken) == [("steps[0].target.selectors[0].exact", "must be true or false")]


def test_bboxes_accept_whole_numbers_as_numbers() -> None:
    target = fingerprint(bbox={"x": 0, "y": 0, "width": 1, "height": 1})

    parsed = step_target(version(steps=[click_step(target=target)]).steps[0])

    assert parsed is not None
    assert parsed.bbox is not None
    assert (parsed.bbox.x, parsed.bbox.width) == (0.0, 1.0)


def test_the_canonical_document_omits_defaults_and_keeps_model_order() -> None:
    parsed = version()

    canonical = workflow_document(parsed)

    assert list(canonical) == ["schema_version", "workflow_id", "version", "created_at", "steps"]
    assert canonical["created_at"] == "2026-09-11T08:30:00Z"
    assert parse_workflow_document(canonical) == parsed


def test_problems_are_reported_together() -> None:
    broken = document(workflow_id="Bad Id", version=0, steps=[click_step(risk="maybe", intent="")])

    assert [path for path, _ in problems(broken)] == [
        "workflow_id",
        "version",
        "steps[0].intent",
        "steps[0].risk",
    ]


def test_an_unknown_top_level_field_is_named() -> None:
    assert problems(document(step=[])) == [("step", "unknown field 'step'; did you mean 'steps'?")]


def test_an_unknown_field_on_a_value_names_the_allowed_fields() -> None:
    broken = document(
        steps=[click_step(), {**click_step("other"), "target": fingerprint(colour="red")}]
    )

    assert problems(broken) == [
        (
            "steps[1].target.colour",
            "'colour' is not a field of this mapping; allowed: tag, role, accessible_name, text, "
            "label_text, attributes, nearby_text, structural_path, bbox, selectors",
        )
    ]
