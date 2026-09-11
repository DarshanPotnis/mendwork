"""Every input and secret reference matches a declaration, and every declaration is used."""

from typing import Any

from tests.workflows import (
    click_step,
    document,
    fill_step,
    input_ref,
    navigate_step,
    problems,
    secret_ref,
    version,
)


def test_declared_and_used_references_are_accepted() -> None:
    parsed = version(
        inputs=[{"name": "portal_url", "kind": "url"}, {"name": "full_name", "kind": "text"}],
        secrets=["api_token"],
        steps=[
            navigate_step(value=input_ref("portal_url")),
            fill_step(value=input_ref("full_name")),
            fill_step("fill_token", value=secret_ref("api_token")),
        ],
    )

    assert [declaration.name for declaration in parsed.inputs] == ["portal_url", "full_name"]


def test_all_reference_problems_are_reported_together_with_exact_paths() -> None:
    broken: dict[str, Any] = document(
        inputs=[
            {"name": "full_name", "kind": "text"},
            {"name": "start", "kind": "date"},
            {"name": "full_name", "kind": "text"},
            {"name": "unused", "kind": "text"},
        ],
        secrets=["api_token", "api_token", "start", "forgotten"],
        steps=[
            navigate_step(value=input_ref("start")),
            fill_step(value=input_ref("ful_name")),
            fill_step("fill_token", value=secret_ref("api_tokn")),
            click_step(),
            click_step(),
            fill_step("fill_again", value=input_ref("full_name")),
        ],
    )

    assert problems(broken) == [
        ("steps[4].id", "duplicate step id 'save' (already used by steps[3])"),
        ("inputs[2].name", "input 'full_name' is declared more than once"),
        ("secrets[1]", "secret 'api_token' is declared more than once"),
        ("secrets[2]", "'start' is declared both as an input and as a secret"),
        ("steps[0].value.name", "a navigate step needs a url input, but 'start' is a date input"),
        (
            "steps[1].value.name",
            "input 'ful_name' is not declared under inputs; did you mean 'full_name'?",
        ),
        (
            "steps[2].value.name",
            "secret 'api_tokn' is not declared under secrets; did you mean 'api_token'?",
        ),
        ("inputs[3].name", "input 'unused' is declared but no step uses it"),
        ("secrets[0]", "secret 'api_token' is declared but no step uses it"),
        ("secrets[1]", "secret 'api_token' is declared but no step uses it"),
        ("secrets[2]", "secret 'start' is declared but no step uses it"),
        ("secrets[3]", "secret 'forgotten' is declared but no step uses it"),
    ]


def test_an_optional_url_input_with_a_default_may_drive_navigation() -> None:
    parsed = version(
        inputs=[
            {
                "name": "home",
                "kind": "url",
                "required": False,
                "default": "https://portal.example.test/",
            }
        ],
        steps=[navigate_step(value=input_ref("home"))],
    )

    assert parsed.inputs[0].default == "https://portal.example.test/"
