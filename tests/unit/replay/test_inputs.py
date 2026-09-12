"""Run inputs bind to declarations: required, optional defaults, unknown names, invalid values."""

from typing import Any

import pytest
from pydantic import TypeAdapter

from mendwork.engine.domain.values import InputDeclaration
from mendwork.engine.errors import RunInputError
from mendwork.engine.replay.inputs import bind_inputs

DECLARATION: TypeAdapter[InputDeclaration] = TypeAdapter(InputDeclaration)


def declarations(*raw: dict[str, Any]) -> list[InputDeclaration]:
    return [DECLARATION.validate_python(item) for item in raw]


PORTAL = {"name": "portal_url", "kind": "url"}
FROM = {"name": "date_from", "kind": "date", "required": False, "default": "2026-02-10"}
EMAIL = {"name": "account_email", "kind": "text"}


def problems(error: RunInputError) -> list[tuple[str, str]]:
    return [(issue.path, issue.message) for issue in error.issues]


def test_supplied_values_and_defaults_are_bound() -> None:
    bound = bind_inputs(declarations(PORTAL, FROM), {"portal_url": "https://a.test/"})

    assert bound == {"portal_url": "https://a.test/", "date_from": "2026-02-10"}


def test_a_supplied_value_replaces_a_default() -> None:
    assert bind_inputs(declarations(FROM), {"date_from": "2026-03-01"}) == {
        "date_from": "2026-03-01"
    }


def test_every_problem_is_reported_at_once_without_echoing_values() -> None:
    with pytest.raises(RunInputError) as caught:
        bind_inputs(
            declarations(PORTAL, FROM, EMAIL),
            {
                "portal_ur": "https://a.test/",
                "date_from": "2026-02-30",
                "account_email": "a@b.test",
            },
        )

    assert problems(caught.value) == [
        (
            "inputs.portal_ur",
            "'portal_ur' is not an input of this workflow; did you mean 'portal_url'?",
        ),
        ("inputs.portal_url", "is required: supply it with --input portal_url=<url>"),
        ("inputs.date_from", "the value is not a real calendar date"),
    ]
    assert "2026-02-30" not in str(caught.value)


def test_an_unknown_name_lists_the_declared_inputs_when_nothing_is_close() -> None:
    with pytest.raises(RunInputError) as caught:
        bind_inputs(declarations(PORTAL, EMAIL), {"zzz": "1"})

    assert problems(caught.value)[0] == (
        "inputs.zzz",
        "'zzz' is not an input of this workflow; its inputs are account_email, portal_url",
    )


def test_a_workflow_without_inputs_says_so() -> None:
    with pytest.raises(RunInputError) as caught:
        bind_inputs([], {"anything": "1"})

    assert problems(caught.value) == [
        ("inputs.anything", "'anything' is not an input of this workflow; it declares no inputs")
    ]


def test_values_are_validated_for_their_kind() -> None:
    with pytest.raises(RunInputError) as caught:
        bind_inputs(declarations(PORTAL), {"portal_url": "ftp://a.test/"})

    assert problems(caught.value) == [
        ("inputs.portal_url", "must be an absolute http or https URL, such as https://example.com/")
    ]
