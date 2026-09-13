"""Words for recorded steps: descriptions, intents, and step ids, all without AI.

A description is what a person checks in the summary ("CLICK the 'Export ledger' button");
an intent is what the workflow file says the step is for ("Click the 'Export ledger'
button"). Step ids are slugs built from the same words, unique within the recording.
"""

import re
from collections.abc import Collection
from typing import Final
from urllib.parse import urlsplit

from mendwork.engine.domain.credentials import tokenize
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.identifiers import StepId, is_slug
from mendwork.engine.domain.limits import IDENTIFIER_MAX_LENGTH
from mendwork.engine.ports.recording_types import PressKey

NAME_MAX_LENGTH: Final = 80
_SLUG_WORDS_MAX_LENGTH: Final = 48
_ASCII_WORD: Final = re.compile(r"[a-z0-9]+")
_ROLE_WORDS: Final = {
    "button": "button",
    "cell": "cell",
    "checkbox": "checkbox",
    "columnheader": "column header",
    "combobox": "list",
    "gridcell": "cell",
    "heading": "heading",
    "img": "image",
    "link": "link",
    "listbox": "list",
    "listitem": "item",
    "menuitem": "menu item",
    "menuitemcheckbox": "menu item",
    "menuitemradio": "menu item",
    "option": "option",
    "radio": "radio button",
    "row": "row",
    "searchbox": "search field",
    "slider": "slider",
    "spinbutton": "field",
    "switch": "switch",
    "tab": "tab",
    "textbox": "field",
    "treeitem": "item",
}
_TAG_WORDS: Final = {
    "a": "link",
    "button": "button",
    "input": "field",
    "select": "list",
    "textarea": "field",
}


def role_word(role: str | None, tag: str) -> str:
    """What a person calls an element with this role or tag."""
    if role is not None and role in _ROLE_WORDS:
        return _ROLE_WORDS[role]
    return _TAG_WORDS.get(tag, "element")


def target_phrase(role: str | None, name: str | None, tag: str) -> str:
    """The target in words: "the 'Export ledger' button", or "the unnamed button"."""
    word = role_word(role, tag)
    if not name:
        return f"the unnamed {word}"
    return f"the '{_shorten(name)}' {word}"


def navigate_words(url: str, title: str) -> tuple[str, str]:
    """The description and intent of opening a URL.

    The description shows the URL, for the person checking the recording. The intent names
    the page by its title, or by its path when it has none, never by its full URL: the URL
    often becomes a run input, and its host and port belong to one environment.
    """
    description = f"NAVIGATE to {_shorten(url, 160)}"
    if title.strip():
        return description, f"Open the '{_shorten(title)}' page"
    return description, f"Open {_shorten(urlsplit(url).path or '/', 160)}"


def target_words(
    action: ActionType, *, role: str | None, name: str | None, tag: str, key: PressKey | None
) -> tuple[str, str]:
    """The description and intent of an action on a target (or on the page, for a key)."""
    phrase = target_phrase(role, name, tag)
    match action:
        case ActionType.CLICK:
            return f"CLICK {phrase}", f"Click {phrase}"
        case ActionType.FILL:
            return f"FILL {phrase}", f"Fill {phrase}"
        case ActionType.SELECT:
            return f"SELECT an option in {phrase}", f"Select an option in {phrase}"
        case ActionType.PRESS:
            pressed = key.value if key is not None else "a key"
            if tag == "":
                return f"PRESS {pressed}", f"Press {pressed}"
            return f"PRESS {pressed} on {phrase}", f"Press {pressed} on {phrase}"
        case ActionType.NAVIGATE:
            raise ValueError("a navigate step has no target; use navigate_words")


def step_id(prefix: str, words: str, taken: Collection[str]) -> StepId:
    """A unique slug such as ``click_download_csv``, suffixed ``_2``, ``_3``… when taken."""
    slug_words: list[str] = []
    length = 0
    for token in tokenize(words):
        for word in _ASCII_WORD.findall(token):
            if length + len(word) + 1 > _SLUG_WORDS_MAX_LENGTH:
                break
            slug_words.append(word)
            length += len(word) + 1
    base = "_".join([prefix, *slug_words])
    candidate = base
    counter = 2
    while candidate in taken or not is_slug(candidate):
        candidate = f"{base}_{counter}"[:IDENTIFIER_MAX_LENGTH]
        counter += 1
    return StepId(candidate)


def _shorten(text: str, limit: int = NAME_MAX_LENGTH) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
