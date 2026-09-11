"""Run inputs are declared with typed values; optional inputs always carry a default."""

from typing import Any

import pytest

from mendwork.engine.domain.enums import InputKind
from mendwork.engine.domain.values import DateInput, TextInput, UrlInput, parse_input_value
from tests.workflows import document, fill_step, input_ref, navigate_step, problems, version


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        (InputKind.TEXT, ""),
        (InputKind.TEXT, "Line one\nline two\tindented"),
        (InputKind.DATE, "2026-02-28"),
        (InputKind.DATE, "2028-02-29"),
        (InputKind.URL, "http://127.0.0.1:8765/index.html"),
        (InputKind.URL, "https://portal.example.test/sign-in?next=%2Forders"),
    ],
)
def test_valid_run_values_are_accepted(kind: InputKind, raw: str) -> None:
    assert parse_input_value(kind, raw) == raw


@pytest.mark.parametrize(
    ("kind", "raw", "message"),
    [
        (InputKind.TEXT, "x" * 4097, "must be at most 4096 characters"),
        (InputKind.TEXT, "bell\x07", "must not contain control characters"),
        (InputKind.DATE, "20260228", "must be a date written as YYYY-MM-DD"),
        (InputKind.DATE, "2026-2-28", "must be a date written as YYYY-MM-DD"),
        (InputKind.DATE, "2026-02-30", "2026-02-30 is not a real calendar date"),
        (InputKind.URL, "portal.example.test", "must be an absolute http or https URL"),
        (InputKind.URL, "ftp://portal.example.test/", "must be an absolute http or https URL"),
        (InputKind.URL, "javascript:alert(1)", "must be an absolute http or https URL"),
        (
            InputKind.URL,
            "https://user:pw@portal.example.test/",
            "must not contain a username or password",
        ),
        (
            InputKind.URL,
            "https://portal.example.test:99999/",
            "must be a valid absolute http or https URL",
        ),
        (InputKind.URL, "https://portal example.test/", "must not contain whitespace"),
        (InputKind.URL, "https://[::1/", "must be a valid absolute http or https URL"),
        (InputKind.URL, "", "must not be blank"),
    ],
)
def test_invalid_run_values_are_explained(kind: InputKind, raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message.replace("?", r"\?")):
        parse_input_value(kind, raw)


def uses(*names: str) -> list[dict[str, Any]]:
    return [fill_step(f"fill_{name}", value=input_ref(name)) for name in names]


def test_an_optional_input_declares_a_typed_default() -> None:
    parsed = version(
        inputs=[
            {"name": "note", "kind": "text", "required": False, "default": ""},
            {"name": "start", "kind": "date", "required": False, "default": "2026-01-01"},
            {
                "name": "home",
                "kind": "url",
                "required": False,
                "default": "https://portal.example.test/",
            },
        ],
        steps=uses("note", "start", "home"),
    )

    assert [type(declaration) for declaration in parsed.inputs] == [TextInput, DateInput, UrlInput]
    assert [declaration.default for declaration in parsed.inputs] == [
        "",
        "2026-01-01",
        "https://portal.example.test/",
    ]


@pytest.mark.parametrize(
    ("declaration", "path", "message"),
    [
        (
            {"name": "start", "kind": "date", "required": False},
            "inputs[0]",
            "an optional input (required: false) must declare a default",
        ),
        (
            {"name": "start", "kind": "date", "default": "2026-01-01"},
            "inputs[0]",
            "a required input must not declare a default; set required: false",
        ),
        (
            {"name": "start", "kind": "date", "required": False, "default": "01/02/2026"},
            "inputs[0].default",
            "must be a date written as YYYY-MM-DD",
        ),
        (
            {"name": "start", "kind": "url", "required": False, "default": "portal.example.test"},
            "inputs[0].default",
            "must be an absolute http or https URL, such as https://example.com/",
        ),
        (
            {"name": "start", "kind": "number"},
            "inputs[0]",
            "kind must be one of: text, date, url (got 'number')",
        ),
    ],
)
def test_declaration_mistakes_are_explained(
    declaration: dict[str, Any], path: str, message: str
) -> None:
    assert problems(document(inputs=[declaration], steps=uses("start"))) == [(path, message)]


def test_a_literal_navigation_url_must_be_absolute() -> None:
    broken = document(steps=[navigate_step(value={"kind": "literal", "value": "/relative"})])

    assert problems(broken) == [
        ("steps[0].value", "must be an absolute http or https URL, such as https://example.com/")
    ]


def test_secrets_are_rejected_outside_fill_steps() -> None:
    broken = document(
        secrets=["portal_url"],
        steps=[navigate_step(value={"kind": "secret", "name": "portal_url"})],
    )

    assert problems(broken) == [
        (
            "steps[0].value",
            "kind must be one of: literal, input (got 'secret'); secret references are only "
            "allowed in fill steps",
        )
    ]
