"""Descriptions of run inputs: a default derived from the recording, or the person's own.

A person naming an input may add a description after the name ("start_url: sign-in page
of the portal"). Without one, the input gets a default built from what was recorded: the
start URL's path for a URL, or the field's label for a typed value. The host and port of a
recorded URL are never part of a default; they belong to one environment.
"""

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from mendwork.engine.domain.base import Text
from mendwork.engine.domain.enums import ActionType, InputKind
from mendwork.engine.domain.limits import TEXT_MAX_LENGTH
from mendwork.engine.domain.recording import DraftStep, InputHint

LABEL_MAX_LENGTH: Final = 80
PATH_MAX_LENGTH: Final = 120
_TEXT: Final[TypeAdapter[str]] = TypeAdapter(Text)


@dataclass(frozen=True, slots=True)
class InputAnswer:
    """What a person typed at an input prompt: a name, and perhaps a description."""

    name: str
    """Empty means the proposed name."""
    description: str | None
    """None means the proposed description."""


def split_answer(answer: str) -> InputAnswer:
    """Read ``name``, ``name: description``, or ``: description``; names never contain ':'."""
    name, separator, description = answer.partition(":")
    text = description.strip()
    return InputAnswer(name=name.strip(), description=text if separator and text else None)


def description_problem(description: str) -> str | None:
    """Why a description cannot be stored, or None if it can."""
    try:
        _TEXT.validate_python(description)
    except ValidationError:
        return f"the description must be one line of at most {TEXT_MAX_LENGTH} characters"
    return None


def default_input_description(
    step: DraftStep, value: str, kind: InputKind, hint: InputHint | None
) -> str:
    """A description of the input for the first step that uses the value."""
    if step.action is ActionType.NAVIGATE:
        path = _shorten(urlsplit(value).path or "/", PATH_MAX_LENGTH)
        if hint is InputHint.START_URL:
            return f"URL of the page the workflow starts on (recorded at {path})"
        return f"URL of the page opened at {path}"
    what = _WHAT.get(hint) if hint is not None else None
    if what is None:
        what = "Date" if kind is InputKind.DATE else "Value"
    target = step.target
    label = (target.label_text or target.accessible_name) if target is not None else None
    if step.action is ActionType.SELECT:
        place = f"the '{_shorten(label, LABEL_MAX_LENGTH)}' list" if label else "an unnamed list"
        return f"{what} chosen in {place}"
    place = f"the '{_shorten(label, LABEL_MAX_LENGTH)}' field" if label else "an unnamed field"
    return f"{what} typed into {place}"


_WHAT: Final = {InputHint.EMAIL: "Email address", InputHint.USERNAME: "Username"}


def _shorten(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
