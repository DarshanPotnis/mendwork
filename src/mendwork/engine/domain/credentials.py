"""Recognising fields that hold credentials, so their values are never stored in a workflow.

A FILL into such a field must use a secret reference. A literal would sit in the workflow
file, and a run input would sit in run history. Detection sees only the fingerprint, and
when the clues are ambiguous it errs towards "secret": a false positive costs a secret
reference, a false negative leaks a password.
"""

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

from mendwork.engine.domain.fingerprint import Fingerprint

_SECRET_AUTOCOMPLETE: Final = frozenset({"current-password", "new-password", "one-time-code"})

# Input types whose values are never credentials, whatever their label says. tel and
# number are absent on purpose: PINs and one-time codes use them.
_NON_SECRET_TYPES: Final = frozenset(
    {
        "checkbox",
        "color",
        "date",
        "datetime-local",
        "email",
        "file",
        "month",
        "radio",
        "range",
        "search",
        "time",
        "url",
        "week",
    }
)

_SECRET_TOKENS: Final = frozenset(
    {
        "apikey",
        "contraseña",
        "cvc",
        "cvv",
        "kennwort",
        "otp",
        "passcode",
        "passphrase",
        "passwd",
        "password",
        "passwort",
        "pin",
        "pw",
        "pwd",
        "secret",
        "senha",
        "token",
        "totp",
        "wachtwoord",
        "пароль",
    }
)
_SECRET_PHRASES: Final = (
    ("access", "token"),
    ("api", "key"),
    ("mot", "de", "passe"),
    ("one", "time", "code"),
    ("private", "key"),
    ("recovery", "code"),
    ("security", "answer"),
    ("security", "code"),
)
# Scripts written without spaces between words tokenize as one long run, so these match
# as substrings instead.
_SECRET_SUBSTRINGS: Final = ("パスワード", "密码", "비밀번호")
_MASK_CHARACTERS: Final = frozenset("•●*·")

_CAMEL_BOUNDARY: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD: Final = re.compile(r"[^\W\d_]+|\d+")


def tokenize(text: str) -> tuple[str, ...]:
    """Split identifiers and labels into comparable words: camelCase, snake, kebab, digits."""
    spaced = _CAMEL_BOUNDARY.sub(" ", text)
    folded = unicodedata.normalize("NFKC", spaced).casefold()
    return tuple(_WORD.findall(folded))


def _names_a_secret(text: str) -> str | None:
    tokens = tokenize(text)
    for token in tokens:
        if token in _SECRET_TOKENS:
            return token
    for phrase in _SECRET_PHRASES:
        width = len(phrase)
        if any(tokens[start : start + width] == phrase for start in range(len(tokens))):
            return " ".join(phrase)
    folded = text.casefold()
    return next((keyword for keyword in _SECRET_SUBSTRINGS if keyword in folded), None)


def _named_clues(fingerprint: Fingerprint) -> Iterable[tuple[str, str | None]]:
    # nearby_text is deliberately absent: the email field sits right next to "Password".
    attributes = fingerprint.attributes
    yield "id", attributes.id
    yield "name", attributes.name
    yield "data_testid", attributes.data_testid
    yield "aria_label", attributes.aria_label
    yield "placeholder", attributes.placeholder
    yield "label_text", fingerprint.label_text
    yield "accessible_name", fingerprint.accessible_name


def detect_secret_field(fingerprint: Fingerprint) -> str | None:
    """Return why a target looks like a credential field, or None if it does not.

    The reason is shown to the person fixing the workflow, so it names the clue.
    """
    attributes = fingerprint.attributes
    input_type = (attributes.type or "").casefold()
    if input_type == "password":
        return 'its type is "password"'
    autocomplete = (attributes.autocomplete or "").casefold()
    if autocomplete in _SECRET_AUTOCOMPLETE:
        return f'its autocomplete is "{autocomplete}"'
    if input_type in _NON_SECRET_TYPES:
        return None
    for clue, value in _named_clues(fingerprint):
        if value is not None and (keyword := _names_a_secret(value)) is not None:
            return f'its {clue} contains "{keyword}"'
    placeholder = attributes.placeholder
    if placeholder is not None and set(placeholder.strip()) <= _MASK_CHARACTERS:
        return "its placeholder is masked"
    return None
