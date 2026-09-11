"""Turning decoded documents into workflow versions and back.

A document is plain data (mappings, lists, scalars) decoded by an adapter from whatever
format it was stored in. The engine decides what a valid document is; adapters only
decode and encode.
"""

import json
from typing import Final

from pydantic import JsonValue, ValidationError

from mendwork.engine.domain.issues import issues_from_validation_error
from mendwork.engine.domain.workflow import CURRENT_SCHEMA_VERSION, WorkflowVersion
from mendwork.engine.errors import (
    UnsupportedSchemaVersion,
    ValidationIssue,
    WorkflowValidationError,
)

SUPPORTED_SCHEMA_VERSIONS: Final = (CURRENT_SCHEMA_VERSION,)


def _supported() -> str:
    return ", ".join(str(version) for version in SUPPORTED_SCHEMA_VERSIONS)


def check_schema_version(document: JsonValue) -> None:
    """Refuse a document whose format this build cannot read, before validating anything else.

    Checked first so a newer file produces one clear error instead of a list of fields
    this build does not recognise.
    """
    if not isinstance(document, dict):
        raise WorkflowValidationError(
            "a workflow document must be a mapping",
            issues=(
                ValidationIssue(
                    (),
                    "a workflow file must be a mapping of fields, starting with "
                    f"'schema_version: {CURRENT_SCHEMA_VERSION}'",
                ),
            ),
        )
    location = ("schema_version",)
    if "schema_version" not in document:
        raise WorkflowValidationError(
            "the workflow document has no schema_version",
            issues=(
                ValidationIssue(
                    location, f"is required; this Mendwork reads version {_supported()}"
                ),
            ),
        )
    found = document["schema_version"]
    if isinstance(found, bool) or not isinstance(found, int):
        raise WorkflowValidationError(
            "schema_version is not a whole number",
            issues=(ValidationIssue(location, "must be a whole number, such as 1"),),
        )
    if found not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersion(
            f"workflow schema version {found} is not supported",
            found=found,
            supported=SUPPORTED_SCHEMA_VERSIONS,
            issues=(
                ValidationIssue(
                    location,
                    f"schema version {found} is not supported; this Mendwork reads version "
                    f"{_supported()}",
                ),
            ),
        )


def parse_workflow_document(document: JsonValue) -> WorkflowVersion:
    """Validate a decoded document strictly and return the workflow version it describes.

    Validation runs in pydantic's strict JSON mode, so "5000" is not a number and 42 is
    not text: formats like YAML guess scalar types, and a guess must not pass silently.
    """
    check_schema_version(document)
    try:
        return WorkflowVersion.model_validate_json(
            json.dumps(document, allow_nan=False), strict=True
        )
    except ValidationError as error:
        issues = issues_from_validation_error(error, document)
        raise WorkflowValidationError(
            f"the workflow has {len(issues)} problem(s)", issues=issues
        ) from error


def workflow_document(version: WorkflowVersion) -> dict[str, JsonValue]:
    """The canonical document for a version: field order follows the model, defaults omitted.

    Omitting defaults keeps files readable; it also makes every default part of the
    schema version, so changing one requires a new schema version.
    """
    return version.model_dump(mode="json", exclude_defaults=True)
