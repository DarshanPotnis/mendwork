"""Redaction of sensitive values from log events.

Redaction is keyed on field *names*, not values: a value is only recognised as a
secret once it has been placed under a name that says so. That keeps the rule cheap
and total — it holds for values the engine has never seen, including secrets resolved
at the moment of use and values typed into FILL steps.
"""

from collections.abc import Iterable, Mapping
from typing import Final

from structlog.typing import EventDict, Processor, WrappedLogger

REDACTED: Final = "[REDACTED]"

DEFAULT_SENSITIVE_KEY_FRAGMENTS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    }
)


def _is_sensitive(key: object, fragments: tuple[str, ...]) -> bool:
    folded = str(key).casefold()
    return any(fragment in folded for fragment in fragments)


def _redact_entry(key: object, value: object, fragments: tuple[str, ...]) -> object:
    if _is_sensitive(key, fragments):
        return REDACTED
    return _redact_value(value, fragments)


def _redact_value(value: object, fragments: tuple[str, ...]) -> object:
    if isinstance(value, Mapping):
        return {key: _redact_entry(key, item, fragments) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, fragments) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, fragments) for item in value)
    return value


def make_redaction_processor(
    sensitive_key_fragments: Iterable[str] = DEFAULT_SENSITIVE_KEY_FRAGMENTS,
) -> Processor:
    """Build a structlog processor that redacts values stored under sensitive keys.

    Matching is case-insensitive and by substring, so ``API_Key`` and ``user_password``
    are both caught, and it recurses through nested mappings, lists, and tuples.
    """
    fragments = tuple(sorted({fragment.casefold() for fragment in sensitive_key_fragments}))

    def redaction_processor(
        logger: WrappedLogger, method_name: str, event_dict: EventDict
    ) -> EventDict:
        return {key: _redact_entry(key, value, fragments) for key, value in event_dict.items()}

    return redaction_processor
