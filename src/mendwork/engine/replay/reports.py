"""Turning errors and page observations into run evidence, scrubbed of secrets.

Scrubbing happens here, where text from outside the engine (browser error messages, page
names, URLs, alert text) enters a record. Authored workflow text and the engine's own
vocabulary are not scrubbed: they cannot hold a value that was only resolved at run time.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue, ValidationError

from mendwork.engine.domain.runs import ErrorCategory, ErrorReport, IdentityReport, TargetEvidence
from mendwork.engine.errors import InfrastructureError, MendworkError
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.safety.secret_scrub import SecretScrubber

TARGET_CONTEXT_KEY: Final = "target"
"""Rung 0 errors carry their evidence under this context key, as JSON."""
DETAIL_MAX_LENGTH: Final = 256


def to_json_value(value: object) -> JsonValue:
    """An error context value as JSON: containers recursed, anything else as text."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_json_value(item) for item in value]
    return str(value)


def error_report(error: MendworkError, scrubber: SecretScrubber) -> ErrorReport:
    """An error as run evidence; its Rung 0 evidence is reported separately."""
    context = {
        key: to_json_value(value)
        for key, value in error.context.items()
        if key != TARGET_CONTEXT_KEY
    }
    scrubbed = scrubber.scrub(context)
    return ErrorReport(
        type=type(error).__name__,
        message=scrubber.scrub_text(error.message),
        category=(
            ErrorCategory.INFRASTRUCTURE
            if isinstance(error, InfrastructureError)
            else ErrorCategory.STEP
        ),
        context=scrubbed if isinstance(scrubbed, dict) else {},
    )


def target_evidence(error: MendworkError) -> TargetEvidence | None:
    """The Rung 0 evidence an error carries, if any."""
    raw = error.context.get(TARGET_CONTEXT_KEY)
    if raw is None:
        return None
    try:
        return TargetEvidence.model_validate(raw)
    except ValidationError as invalid:
        raise MendworkError(
            "an error carried malformed target evidence", error_type=type(error).__name__
        ) from invalid


def identity_report(identity: ElementIdentity, scrubber: SecretScrubber) -> IdentityReport:
    """A page-computed identity as evidence, scrubbed."""
    return IdentityReport(
        tag=scrubber.scrub_text(identity.tag),
        input_type=None
        if identity.input_type is None
        else scrubber.scrub_text(identity.input_type),
        role=None if identity.role is None else scrubber.scrub_text(identity.role),
        name=scrubber.scrub_text(identity.name),
        confirmed=identity.confirmed,
    )


def detail(text: str, scrubber: SecretScrubber) -> str:
    """Page-derived text for a checkpoint result: scrubbed, then shortened."""
    scrubbed = scrubber.scrub_text(text)
    if len(scrubbed) <= DETAIL_MAX_LENGTH:
        return scrubbed
    return scrubbed[: DETAIL_MAX_LENGTH - 1] + "…"
