"""Where step values come from, and the declarations that make every reference checkable.

A workflow declares its run inputs and secret names up front. Every reference must match
a declaration and every declaration must be used, so a typo in a name fails when the file
loads instead of when a run reaches the step.
"""

import re
from datetime import date
from typing import Annotated, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, model_validator

from mendwork.engine.domain.base import (
    DomainModel,
    LiteralText,
    LongText,
    Text,
    check_literal_text,
    check_single_line,
)
from mendwork.engine.domain.enums import InputKind, ValueKind
from mendwork.engine.domain.identifiers import InputNameField, SecretNameField
from mendwork.engine.domain.limits import LITERAL_VALUE_MAX_LENGTH, LONG_TEXT_MAX_LENGTH

_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")


def check_iso_date(value: str) -> str:
    """Accept a calendar date written exactly as YYYY-MM-DD."""
    # fromisoformat alone also accepts the basic format "20260101".
    if _ISO_DATE.fullmatch(value) is None:
        raise ValueError("must be a date written as YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{value} is not a real calendar date") from None
    return value


def check_http_url(value: str) -> str:
    """Accept an absolute http(s) URL with a host and no embedded credentials."""
    if any(character.isspace() for character in value):
        raise ValueError("must not contain whitespace")
    try:
        parts = urlsplit(value)
        # Reading the port validates it; urlsplit alone accepts "host:99999".
        _ = parts.port
    except ValueError:
        raise ValueError("must be a valid absolute http or https URL") from None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("must be an absolute http or https URL, such as https://example.com/")
    if parts.username is not None or parts.password is not None:
        raise ValueError("must not contain a username or password; use a secret instead")
    return value


IsoDate = Annotated[str, AfterValidator(check_iso_date)]
HttpUrl = Annotated[LongText, AfterValidator(check_http_url)]


def parse_input_value(kind: InputKind, raw: str) -> str:
    """Validate a run-time value supplied for an input of the given kind.

    The same rules apply to a declared default, so a default is never a value a run could
    not have been given.
    """
    match kind:
        case InputKind.TEXT:
            _check_length(raw, LITERAL_VALUE_MAX_LENGTH)
            return check_literal_text(raw)
        case InputKind.DATE:
            return check_iso_date(raw)
        case InputKind.URL:
            _check_length(raw, LONG_TEXT_MAX_LENGTH)
            return check_http_url(check_single_line(raw))


def _check_length(value: str, limit: int) -> None:
    if len(value) > limit:
        raise ValueError(f"must be at most {limit} characters")


class LiteralValue(DomainModel):
    """A value written into the workflow itself."""

    kind: Literal[ValueKind.LITERAL]
    value: LiteralText


class InputValue(DomainModel):
    """A value supplied when a run starts; it is recorded in run history."""

    kind: Literal[ValueKind.INPUT]
    name: InputNameField


class SecretValue(DomainModel):
    """A value resolved only in memory, at the moment of use, and never stored or logged."""

    kind: Literal[ValueKind.SECRET]
    name: SecretNameField


ValueRef = Annotated[LiteralValue | InputValue | SecretValue, Field(discriminator="kind")]
NonSecretValueRef = Annotated[LiteralValue | InputValue, Field(discriminator="kind")]


class _InputDeclaration(DomainModel):
    name: InputNameField
    kind: InputKind
    required: bool = True
    """An optional input must declare a default, so no step ever runs without a value."""
    default: str | None = None
    """The value used when a run does not supply one; only optional inputs have one."""
    description: Text | None = None

    @model_validator(mode="after")
    def _default_matches_required(self) -> Self:
        default = self.default
        if self.required and default is not None:
            raise ValueError("a required input must not declare a default; set required: false")
        if not self.required and default is None:
            raise ValueError("an optional input (required: false) must declare a default")
        return self


class TextInput(_InputDeclaration):
    """A free-text run input."""

    kind: Literal[InputKind.TEXT]
    default: LiteralText | None = None


class DateInput(_InputDeclaration):
    """A calendar date run input, written YYYY-MM-DD."""

    kind: Literal[InputKind.DATE]
    default: IsoDate | None = None


class UrlInput(_InputDeclaration):
    """An absolute http(s) URL run input."""

    kind: Literal[InputKind.URL]
    default: HttpUrl | None = None


InputDeclaration = Annotated[TextInput | DateInput | UrlInput, Field(discriminator="kind")]
