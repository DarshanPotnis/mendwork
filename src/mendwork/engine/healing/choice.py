"""Reading a model's reply: exactly one JSON object with a choice, a confidence, and a reason.

Parsing is strict and total. Anything but a single JSON object whose only fields are ``choice``
(an integer or null), ``confidence`` (a number from 0 to 1), and ``reason`` (non-blank text of
bounded length) is a problem with a fixed description, never a best guess:

- no stripping of Markdown fences or surrounding prose;
- duplicate keys, NaN, and Infinity are refused;
- no coercion: ``"2"``, ``2.0``, and ``true`` are not choices.

Every adapter parses with this one function, so every provider is held to the same shape.
Whether a choice is on the list is decided separately, by the rung, because an out-of-range
answer is not repaired: it is treated as no answer.
"""

import json
import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Final

from pydantic import ConfigDict, Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.healing.prompt import REASON_MAX_CHARS

_FIELDS: Final = frozenset({"choice", "confidence", "reason"})


class ChoiceProblem(StrEnum):
    """What was wrong with a reply, in words a repair request can show the model."""

    EMPTY = "it was empty"
    NOT_ONE_OBJECT = "it was not exactly one JSON object"
    FIELDS = "it did not have exactly the fields choice, confidence, and reason"
    CHOICE = "choice must be one of the listed numbers, or null"
    CONFIDENCE = "confidence must be a number from 0 to 1"
    REASON = f"reason must be one sentence of at most {REASON_MAX_CHARS} characters"
    CUT_OFF = "it was cut off before it ended"
    WITHHELD = "the provider withheld the answer"


class ParsedChoice(DomainModel):
    """A reply in the required shape."""

    model_config = ConfigDict(strict=True)

    choice: int | None
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=REASON_MAX_CHARS)


class _DuplicateKeyError(ValueError):
    """A JSON object named the same key twice, which a lenient parser would silently resolve."""


class _NonFiniteNumberError(ValueError):
    """A JSON number was NaN or infinite, which JSON itself does not allow."""


def parse_choice(text: str) -> ParsedChoice | ChoiceProblem:
    """The reply as a choice, or what was wrong with it."""
    stripped = text.strip()
    if not stripped:
        return ChoiceProblem.EMPTY
    try:
        document = json.loads(
            stripped, object_pairs_hook=_without_duplicates, parse_constant=_refuse_constant
        )
    except (json.JSONDecodeError, _DuplicateKeyError, _NonFiniteNumberError):
        return ChoiceProblem.NOT_ONE_OBJECT
    if not isinstance(document, dict):
        return ChoiceProblem.NOT_ONE_OBJECT
    if set(document) != _FIELDS:
        return ChoiceProblem.FIELDS
    choice = document["choice"]
    confidence = document["confidence"]
    reason = document["reason"]
    if choice is not None and (isinstance(choice, bool) or not isinstance(choice, int)):
        return ChoiceProblem.CHOICE
    if not _is_unit_number(confidence):
        return ChoiceProblem.CONFIDENCE
    if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX_CHARS:
        return ChoiceProblem.REASON
    return ParsedChoice.model_validate(
        {"choice": choice, "confidence": float(confidence), "reason": reason}, strict=True
    )


def on_the_list(choice: int, shown: int) -> bool:
    """Whether a choice names one of ``shown`` numbered candidates."""
    return 1 <= choice <= shown


def _is_unit_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and 0.0 <= value <= 1.0


def _without_duplicates(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _DuplicateKeyError("duplicate key")
    return dict(pairs)


def _refuse_constant(name: str) -> object:
    raise _NonFiniteNumberError(name)
