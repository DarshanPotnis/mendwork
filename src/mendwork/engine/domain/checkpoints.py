"""Checkpoints: what "this step worked" means, observed after the step's action.

Timeouts are optional: absent means the runtime default, so the format never hardcodes
timing. Every regular expression uses Python ``re`` syntax, must match the whole string
(``re.fullmatch``), and is compiled when the workflow loads, so a broken pattern fails at
load time rather than at the moment a run needs it.
"""

import re
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, ValidationInfo, field_validator, model_validator

from mendwork.engine.domain.base import DomainModel, LongText
from mendwork.engine.domain.enums import CheckpointKind, UrlMatchMode
from mendwork.engine.domain.limits import HTTP_STATUS_MAX, HTTP_STATUS_MIN, TIMEOUT_MS_MAX
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.values import check_http_url


def check_regex(pattern: str) -> str:
    """Accept a pattern that compiles, reporting the compiler's reason if it does not."""
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f"is not a valid regular expression: {error}") from None
    return pattern


RegexPattern = Annotated[LongText, AfterValidator(check_regex)]
TimeoutMs = Annotated[int, Field(ge=1, le=TIMEOUT_MS_MAX)]
HttpStatus = Annotated[int, Field(ge=HTTP_STATUS_MIN, le=HTTP_STATUS_MAX)]


class _UrlPattern(DomainModel):
    kind: CheckpointKind
    mode: UrlMatchMode
    """exact: the whole URL. prefix: the URL starts with it. regex: re.fullmatch."""
    pattern: LongText

    @field_validator("pattern")
    @classmethod
    def _check_pattern_for_mode(cls, pattern: str, info: ValidationInfo) -> str:
        mode = info.data.get("mode")
        if mode is UrlMatchMode.REGEX:
            return check_regex(pattern)
        if mode is not None:
            try:
                check_http_url(pattern)
            except ValueError as error:
                raise ValueError(
                    f"{error} (mode is {mode}; use mode: regex for a pattern)"
                ) from None
        return pattern

    def matches_url(self, url: str) -> bool:
        """Whether a URL satisfies this pattern under its mode."""
        match self.mode:
            case UrlMatchMode.EXACT:
                return url == self.pattern
            case UrlMatchMode.PREFIX:
                return url.startswith(self.pattern)
            case UrlMatchMode.REGEX:
                return re.fullmatch(self.pattern, url) is not None


class UrlMatches(_UrlPattern):
    """The page's URL matches a pattern."""

    kind: Literal[CheckpointKind.URL_MATCHES]
    timeout_ms: TimeoutMs | None = None


class ElementVisible(DomainModel):
    """Exactly one element matching the selector is visible."""

    kind: Literal[CheckpointKind.ELEMENT_VISIBLE]
    selector: Selector
    timeout_ms: TimeoutMs | None = None


class TextPresent(DomainModel):
    """Visible text on the page contains this text (whitespace-normalized, case-sensitive)."""

    kind: Literal[CheckpointKind.TEXT_PRESENT]
    text: LongText
    timeout_ms: TimeoutMs | None = None


class DownloadCompleted(DomainModel):
    """A download finished and its suggested filename matches the pattern."""

    kind: Literal[CheckpointKind.DOWNLOAD_COMPLETED]
    filename_pattern: RegexPattern
    timeout_ms: TimeoutMs | None = None


class ResponseReceived(_UrlPattern):
    """A network response arrived from a matching URL with a status in the range."""

    kind: Literal[CheckpointKind.RESPONSE_RECEIVED]
    status_min: HttpStatus
    status_max: HttpStatus
    timeout_ms: TimeoutMs | None = None

    @model_validator(mode="after")
    def _check_status_range(self) -> Self:
        if self.status_min > self.status_max:
            raise ValueError("status_min must not be greater than status_max")
        return self


class NoErrorBanner(DomainModel):
    """No error banner is visible, checked once after the step's other checkpoints pass.

    It has no timeout: waiting for something *not* to appear would be a sleep. Without a
    selector, the runtime's default applies (a visible role=alert element with text).
    """

    kind: Literal[CheckpointKind.NO_ERROR_BANNER]
    selector: Selector | None = None


class FieldHasValue(DomainModel):
    """The fill step's target field now holds the value the step typed.

    Only valid on fill steps. For a secret it checks only that the field is not empty: a
    secret is never read back, compared, or recorded.
    """

    kind: Literal[CheckpointKind.FIELD_HAS_VALUE]
    timeout_ms: TimeoutMs | None = None


Checkpoint = Annotated[
    UrlMatches
    | ElementVisible
    | TextPresent
    | DownloadCompleted
    | ResponseReceived
    | NoErrorBanner
    | FieldHasValue,
    Field(discriminator="kind"),
]
