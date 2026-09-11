"""The shared base for every domain model, and the constrained text types they use.

Domain objects are values: frozen, closed to unknown fields, and never echoing their
input in validation errors, because a FILL value or a mistakenly pasted secret must not
reach a log line through an error message.
"""

import unicodedata
from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from mendwork.engine.domain.limits import (
    LITERAL_VALUE_MAX_LENGTH,
    LONG_TEXT_MAX_LENGTH,
    SHORT_TEXT_MAX_LENGTH,
    TEXT_MAX_LENGTH,
)

# Control characters, surrogates, and Unicode line and paragraph separators. Format
# characters (Cf) stay allowed: real labels contain zero-width joiners and direction marks.
_FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cs", "Zl", "Zp"})
_LITERAL_ALLOWED: Final = frozenset({"\t", "\n"})


class DomainModel(BaseModel):
    """Base class for workflow domain models."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        use_attribute_docstrings=True,
    )


def _reject_forbidden_characters(value: str, allowed: frozenset[str]) -> None:
    for character in value:
        if character not in allowed and unicodedata.category(character) in _FORBIDDEN_CATEGORIES:
            raise ValueError(
                f"must not contain control characters or line breaks (found U+{ord(character):04X})"
            )


def check_single_line(value: str) -> str:
    """Accept text that is one line and not blank."""
    _reject_forbidden_characters(value, frozenset())
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def check_literal_text(value: str) -> str:
    """Accept text typed into a field: tabs and newlines allowed, other controls not."""
    _reject_forbidden_characters(value, _LITERAL_ALLOWED)
    return value


ShortText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=SHORT_TEXT_MAX_LENGTH),
    AfterValidator(check_single_line),
]
Text = Annotated[
    str,
    StringConstraints(min_length=1, max_length=TEXT_MAX_LENGTH),
    AfterValidator(check_single_line),
]
LongText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=LONG_TEXT_MAX_LENGTH),
    AfterValidator(check_single_line),
]
LiteralText = Annotated[
    str,
    StringConstraints(max_length=LITERAL_VALUE_MAX_LENGTH),
    AfterValidator(check_literal_text),
]
