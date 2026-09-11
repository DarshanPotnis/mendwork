"""Typed identifiers.

Workflow ids become directory names and step ids are how a version history refers to the
same logical step, so both are short lowercase slugs: nothing that can escape a path,
collide by case on a case-insensitive filesystem, or look like a version number.
"""

import re
from typing import Annotated, Final, NewType

from pydantic import Field, StringConstraints

from mendwork.engine.domain.limits import IDENTIFIER_MAX_LENGTH
from mendwork.engine.errors import ValidationIssue, WorkflowValidationError

SLUG_PATTERN: Final = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
SLUG_DESCRIPTION: Final = (
    "must be a lowercase slug: letters, digits, and single underscores, starting with a "
    f"letter, at most {IDENTIFIER_MAX_LENGTH} characters"
)

WorkflowId = NewType("WorkflowId", str)
StepId = NewType("StepId", str)
InputName = NewType("InputName", str)
SecretName = NewType("SecretName", str)

_SLUG = StringConstraints(min_length=1, max_length=IDENTIFIER_MAX_LENGTH, pattern=SLUG_PATTERN)

WorkflowIdField = Annotated[WorkflowId, _SLUG]
StepIdField = Annotated[StepId, _SLUG]
InputNameField = Annotated[InputName, _SLUG]
SecretNameField = Annotated[SecretName, _SLUG]
VersionNumber = Annotated[int, Field(ge=1)]

# fullmatch, not the pattern's "$": in Python "$" also matches before a trailing newline.
_SLUG_RE: Final = re.compile(SLUG_PATTERN.removeprefix("^").removesuffix("$"))


def is_slug(value: str) -> bool:
    """Whether a string is a valid identifier slug."""
    return len(value) <= IDENTIFIER_MAX_LENGTH and _SLUG_RE.fullmatch(value) is not None


def parse_workflow_id(value: str) -> WorkflowId:
    """Validate an untrusted string, such as a CLI argument, as a workflow id."""
    if not is_slug(value):
        raise WorkflowValidationError(
            f"invalid workflow id: {SLUG_DESCRIPTION}",
            issues=(ValidationIssue(("workflow_id",), SLUG_DESCRIPTION),),
        )
    return WorkflowId(value)
