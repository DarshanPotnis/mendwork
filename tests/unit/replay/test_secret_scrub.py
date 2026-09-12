"""Secret values are removed from text and detected in bytes, in every covered encoding."""

import base64
import html
import json
from urllib.parse import quote, quote_plus

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue, SecretStr

from mendwork.engine.safety.redaction import REDACTED
from mendwork.engine.safety.secret_scrub import SecretScrubber, byte_variants, text_variants

SECRET = 'hunter2 & "friends"/ü'


def scrubber(*values: str) -> SecretScrubber:
    result = SecretScrubber()
    for value in values:
        result.register(SecretStr(value))
    return result


@pytest.mark.parametrize(
    "encoded",
    [
        SECRET,
        json.dumps(SECRET)[1:-1],
        json.dumps(SECRET, ensure_ascii=True)[1:-1],
        html.escape(SECRET),
        quote(SECRET, safe=""),
        quote_plus(SECRET),
    ],
)
def test_every_text_encoding_is_scrubbed(encoded: str) -> None:
    text = f"before {encoded} after"

    assert scrubber(SECRET).scrub_text(text) == f"before {REDACTED} after"


@pytest.mark.parametrize("prefix", [b"", b"a", b"ab", b"abc"])
def test_base64_is_detected_at_every_byte_alignment(prefix: bytes) -> None:
    payload = prefix + SECRET.encode() + b"tail"

    assert scrubber(SECRET).contains_secret(base64.b64encode(payload))
    assert scrubber(SECRET).contains_secret(base64.urlsafe_b64encode(payload))


def test_utf16_and_percent_encoding_are_detected_in_bytes() -> None:
    detector = scrubber(SECRET)

    assert detector.contains_secret(b"x" + SECRET.encode("utf-16-le") + b"y")
    assert detector.contains_secret(quote(SECRET, safe="").encode())
    assert not detector.contains_secret(b"nothing to see")


def test_a_secret_inside_the_marker_is_deleted_rather_than_reintroduced() -> None:
    assert scrubber("RED").scrub_text("RED alert: RED") == " alert: "


def test_scrubbing_recurses_through_keys_and_lists() -> None:
    value: JsonValue = {"message": f"typed {SECRET}", SECRET: [SECRET, 3, None]}

    assert scrubber(SECRET).scrub(value) == {
        "message": f"typed {REDACTED}",
        REDACTED: [REDACTED, 3, None],
    }


def test_nothing_changes_before_a_secret_is_registered() -> None:
    idle = SecretScrubber()

    assert not idle.active
    assert idle.scrub_text(SECRET) == SECRET
    assert not idle.contains_secret(SECRET.encode())


def test_an_empty_secret_registers_nothing() -> None:
    assert not scrubber("").active


def test_variants_never_include_an_empty_string() -> None:
    assert "" not in text_variants("a")
    assert b"" not in byte_variants("a")


# The utf-8 codec already excludes surrogates, which no secret can contain.
printable = st.text(st.characters(codec="utf-8"), min_size=1, max_size=12)


@given(printable, st.text(max_size=30), st.text(max_size=30))
def test_scrubbed_text_never_contains_the_secret_in_any_form(
    secret: str, before: str, after: str
) -> None:
    result = scrubber(secret).scrub_text(
        before + secret + after + json.dumps(secret) + quote(secret)
    )

    assert all(variant not in result for variant in text_variants(secret))


@given(printable, printable)
def test_two_secrets_are_both_removed(first: str, second: str) -> None:
    result = scrubber(first, second).scrub_text(f"{first}|{second}|{second}{first}")

    assert first not in result
    assert second not in result
