"""Removing secret values from everything a run records or shows.

Log redaction (``redaction.py``) works on field names. That cannot cover text the engine
did not write: a browser error message, a page URL, an alert's text, a DOM snapshot, or a
trace file. Any of those can hold a secret's value verbatim or encoded, so a run registers
each secret the moment it is resolved and scrubs such text by value.

Encodings covered: the raw value, JSON string escaping (with and without ASCII escapes),
HTML escaping, URL percent-encoding (``quote`` and ``quote_plus``), and, for bytes, UTF-16
little-endian and standard and URL-safe base64 at all three byte alignments.
"""

import base64
import html
import json
from collections.abc import Callable, Iterable
from typing import Final
from urllib.parse import quote, quote_plus

from pydantic import JsonValue, SecretStr

from mendwork.engine.safety.redaction import REDACTED

_BASE64_ENCODERS: Final[tuple[Callable[[bytes], bytes], ...]] = (
    base64.b64encode,
    base64.urlsafe_b64encode,
)


def text_variants(value: str) -> frozenset[str]:
    """The forms in which a value can appear inside text."""
    candidates = {
        value,
        json.dumps(value, ensure_ascii=False)[1:-1],
        json.dumps(value, ensure_ascii=True)[1:-1],
        html.escape(value, quote=True),
        quote(value, safe=""),
        quote_plus(value),
    }
    return frozenset(candidate for candidate in candidates if candidate)


def byte_variants(value: str) -> frozenset[bytes]:
    """The forms in which a value can appear inside a file, such as a trace archive member."""
    raw = value.encode("utf-8")
    candidates = {variant.encode("utf-8") for variant in text_variants(value)}
    candidates.add(value.encode("utf-16-le"))
    candidates.update(_base64_cores(raw))
    return frozenset(candidate for candidate in candidates if candidate)


def _base64_cores(raw: bytes) -> set[bytes]:
    # A value embedded in a longer byte string is encoded differently depending on how many
    # bytes precede it (mod 3). For each alignment, keep only the characters whose six bits
    # come entirely from the value, so the core matches wherever the value sits.
    cores: set[bytes] = set()
    for offset in range(3):
        start = -(-offset * 8 // 6)
        end = (offset + len(raw)) * 8 // 6
        for encode in _BASE64_ENCODERS:
            core = encode(bytes(offset) + raw)[start:end]
            if core:
                cores.add(core)
    return cores


class SecretScrubber:
    """Secret values that were resolved, and the means to remove them from output.

    A run has its own, and a process has one more that its log pipeline reads, so a secret any
    run resolves never reaches a log line. It only ever grows: a value typed into a page may be
    reflected in anything captured later. Registering replaces its sets rather than changing them,
    so a reader on another thread, such as a log handler, always sees a whole set.
    """

    def __init__(self) -> None:
        self._text: frozenset[str] = frozenset()
        self._bytes: frozenset[bytes] = frozenset()
        self._outbound: frozenset[str] = frozenset()

    def register(self, secret: SecretStr) -> None:
        """Remember a resolved secret so later output is scrubbed of it."""
        value = secret.get_secret_value()
        if value:
            self._text = self._text | text_variants(value)
            self._bytes = self._bytes | byte_variants(value)
            self._outbound = self._outbound | {
                core.decode("ascii") for core in _base64_cores(value.encode("utf-8"))
            }

    def scrub_outbound(self, text: str) -> str:
        """Text about to leave the machine, such as a model prompt, with every secret removed.

        Beyond ``scrub_text``, a value's base64 forms are deleted too: page text can carry them,
        and whoever receives the text can decode them.
        """
        known, outbound = self._text, self._outbound
        cores = sorted(outbound, key=len, reverse=True)
        result = _scrubbed(text, known)
        for _ in range(len(result) + 1):
            if not any(core in result for core in cores):
                return result
            result = _scrubbed(_replace_all(result, cores, ""), known)
        everything = sorted(known | outbound, key=len, reverse=True)
        while _occurs(result, known) or any(core in result for core in cores):
            result = _replace_all(result, everything, "")
        return result

    @property
    def active(self) -> bool:
        """Whether any secret has been registered."""
        return bool(self._text)

    def contains_secret(self, data: bytes) -> bool:
        """Whether a byte string holds any registered secret in any covered encoding."""
        return any(variant in data for variant in self._bytes)

    def scrub_text(self, text: str) -> str:
        """The text with every registered secret removed."""
        return _scrubbed(text, self._text)

    def scrub(self, value: JsonValue) -> JsonValue:
        """A JSON-like value with every string, keys included, scrubbed."""
        if isinstance(value, str):
            return self.scrub_text(value)
        if isinstance(value, dict):
            return {self.scrub_text(key): self.scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        return value


def _scrubbed(text: str, known: frozenset[str]) -> str:
    """The text with every known variant removed.

    The replacement never reintroduces a secret: if a secret is a substring of the redaction
    marker, occurrences are deleted instead, and deletion strictly shortens the text, so the loop
    always ends.
    """
    if not _occurs(text, known):
        return text
    ordered = sorted(known, key=len, reverse=True)
    replacement = "" if any(variant in REDACTED for variant in ordered) else REDACTED
    result = _replace_all(text, ordered, replacement)
    for _ in range(len(text)):
        if not _occurs(result, known):
            return result
        result = _replace_all(result, ordered, replacement)
    while _occurs(result, known):
        result = _replace_all(result, ordered, "")
    return result


def _occurs(text: str, known: frozenset[str]) -> bool:
    return any(variant in text for variant in known)


def _replace_all(text: str, variants: Iterable[str], replacement: str) -> str:
    for variant in variants:
        text = text.replace(variant, replacement)
    return text
