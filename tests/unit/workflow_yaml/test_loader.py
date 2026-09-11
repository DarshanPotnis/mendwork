"""The strict YAML loader rejects what PyYAML would silently accept, at the right line."""

import pytest

from mendwork.adapters.workflow_yaml.loader import MAX_NESTING_DEPTH, Position, load_yaml
from mendwork.engine.errors import WorkflowValidationError

LIMIT = 1 << 20


def rejection(text: str | bytes, *, max_bytes: int = LIMIT) -> tuple[int | None, int | None, str]:
    content = text.encode() if isinstance(text, str) else text
    with pytest.raises(WorkflowValidationError) as caught:
        load_yaml(content, max_bytes=max_bytes)
    [issue] = caught.value.issues
    return issue.line, issue.column, issue.message


def test_scalars_resolve_under_the_narrow_rules() -> None:
    loaded = load_yaml(
        b"a: null\nb: ~\nc:\nd: true\ne: False\nf: 42\ng: -7\nh: 0.25\ni: 1.0e-05\n"
        b"j: yes\nk: no\nl: on\nm: 2026-01-01\nn: 1e5\no: 012\np: 0x1F\nq: .inf\nr: 1_000\n"
        b"s: 12:30\nt: '42'\nu: \"true\"\nv: -.5\nw: 1.0e5\nx: .5\n",
        max_bytes=LIMIT,
    )

    assert loaded.data == {
        "a": None, "b": None, "c": None, "d": True, "e": False, "f": 42, "g": -7, "h": 0.25,
        "i": 1e-05, "j": "yes", "k": "no", "l": "on", "m": "2026-01-01", "n": "1e5",
        "o": "012", "p": "0x1F", "q": ".inf", "r": "1_000", "s": "12:30", "t": "42", "u": "true",
        "v": "-.5", "w": "1.0e5", "x": 0.5,
    }  # fmt: skip


def test_positions_are_recorded_for_keys_and_items() -> None:
    loaded = load_yaml(b"steps:\n  - id: first\n    intent: x\n  - id: second\n", max_bytes=LIMIT)

    assert loaded.positions[("steps",)] == Position(1, 1)
    assert loaded.positions[("steps", 0)] == Position(2, 5)
    assert loaded.positions[("steps", 0, "intent")] == Position(3, 5)
    assert loaded.positions[("steps", 1, "id")] == Position(4, 5)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("id: a\nintent: b\nid: c\n", (3, 1, "duplicate key 'id' (first defined at line 1)")),
        ("steps:\n  - {id: a, id: b}\n", (2, 13, "duplicate key 'id' (first defined at line 2)")),
        ("base: &shared {x: 1}\nother: *shared\n", (1, 7, "anchors (&name) are not allowed")),
        ("a: 1\nb: *missing\n", (2, 4, "aliases (*name) are not allowed")),
        ("value: !!str 42\n", (1, 8, "tags (such as !!str) are not allowed")),
        ("value: !custom thing\n", (1, 8, "tags (such as !!str) are not allowed")),
        ("? [a, b]\n: value\n", (1, 3, "mapping keys must be plain text")),
        ("1: one\n", (1, 1, "mapping keys must be plain text")),
        ("null: nothing\n", (1, 1, "mapping keys must be plain text")),
        ("a: 1\n---\nb: 2\n", (2, 1, "a workflow file must contain exactly one YAML document")),
        ("", (1, 1, "the file is empty")),
        ("# only a comment\n", (2, 1, "the file is empty")),
        (
            "steps:\n  - id: a\n   intent: b\n",
            (3, 4, "YAML syntax error: expected <block end>, but found '<block mapping start>'"),
        ),
        (
            "a:\n\tb: 1\n",
            (2, 1, "YAML syntax error: found character '\\t' that cannot start any token"),
        ),
    ],
)
def test_unsafe_or_ambiguous_yaml_is_rejected_at_its_line(
    text: str, expected: tuple[int, int, str]
) -> None:
    assert rejection(text) == expected


def test_a_merge_key_needs_an_alias_and_is_rejected_with_it() -> None:
    assert (
        rejection("defaults: &d {risk: safe}\nstep:\n  <<: *d\n")[2]
        == "anchors (&name) are not allowed"
    )


def test_exponential_alias_expansion_is_impossible() -> None:
    bomb = "a: &a [x, x]\nb: &b [*a, *a]\nc: &c [*b, *b]\n"

    assert rejection(bomb)[2] == "anchors (&name) are not allowed"


def test_deep_nesting_is_rejected_before_it_can_exhaust_the_stack() -> None:
    deep = "[" * (MAX_NESTING_DEPTH + 1) + "]" * (MAX_NESTING_DEPTH + 1)

    assert rejection(deep) == (
        1,
        MAX_NESTING_DEPTH + 1,
        f"nests deeper than {MAX_NESTING_DEPTH} levels",
    )


def test_oversized_documents_are_rejected_before_parsing() -> None:
    assert rejection(b"a: " + b"x" * 100, max_bytes=64) == (
        None,
        None,
        "the file is larger than the 64-byte limit",
    )


def test_invalid_utf8_is_located_by_line() -> None:
    assert rejection(b"a: 1\nb: \xff\n") == (2, None, "is not valid UTF-8 (byte 8)")


def test_a_byte_order_mark_is_accepted() -> None:
    assert load_yaml(b"\xef\xbb\xbfa: 1\n", max_bytes=LIMIT).data == {"a": 1}


def test_characters_yaml_forbids_are_reported() -> None:
    line, column, message = rejection("a: \x07\n")

    assert (line, column) == (None, None)
    assert message.startswith("YAML syntax error: unacceptable character #x0007")
