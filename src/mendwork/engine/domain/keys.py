"""Keyboard keys a PRESS step may send, in the syntax Playwright's keyboard accepts.

The grammar is closed on purpose: a typo such as "Entr" fails when the workflow loads,
not halfway through a run.
"""

from typing import Annotated, Final

from pydantic import AfterValidator, StringConstraints

from mendwork.engine.domain.limits import SHORT_TEXT_MAX_LENGTH

MODIFIERS: Final = ("Alt", "Control", "ControlOrMeta", "Meta", "Shift")
NAMED_KEYS: Final = frozenset(
    {
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "Backspace",
        "Delete",
        "End",
        "Enter",
        "Escape",
        "Home",
        "Insert",
        "PageDown",
        "PageUp",
        "Space",
        "Tab",
        *(f"F{number}" for number in range(1, 13)),
    }
)


def _is_character_key(key: str) -> bool:
    # Printable ASCII except space (write "Space") and "+" (the combination separator).
    return len(key) == 1 and "!" <= key <= "~" and key != "+"


def check_key(value: str) -> str:
    """Accept ``Modifier+...+Key``, where Key is a named key or one printable character."""
    *modifiers, key = value.split("+")
    if not (key in NAMED_KEYS or _is_character_key(key)):
        raise ValueError(
            f"{key!r} is not a key: use a named key such as Enter, Tab, Escape, ArrowDown, "
            "F5, or Space, or a single printable character"
        )
    for modifier in modifiers:
        if modifier not in MODIFIERS:
            raise ValueError(f"{modifier!r} is not a modifier: use {', '.join(MODIFIERS)}")
    if len(set(modifiers)) != len(modifiers):
        raise ValueError("a modifier must not repeat")
    return value


KeyName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=SHORT_TEXT_MAX_LENGTH),
    AfterValidator(check_key),
]
