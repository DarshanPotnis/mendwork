"""Fingerprints store only allowlisted, bounded, path-only facts about a target."""

from typing import Any

import pytest

from mendwork.engine.domain.steps import step_target
from tests.workflows import click_step, document, fingerprint, problems, version


def rejected(**target: object) -> list[tuple[str, str]]:
    return problems(document(steps=[click_step(target=fingerprint(**target))]))


def test_a_complete_fingerprint_is_accepted() -> None:
    parsed = version(
        steps=[
            click_step(
                target=fingerprint(
                    tag="a",
                    role="link",
                    text="View reports",
                    label_text=None,
                    attributes={
                        "id": "open-reports",
                        "name": "reports",
                        "type": "button",
                        "autocomplete": "off",
                        "placeholder": "Reports",
                        "aria_label": "View reports",
                        "data_testid": "dashboard-open-reports",
                        "href": "/reports.html",
                    },
                    nearby_text=["Shipment reports"],
                    bbox={"x": 0.6446, "y": 0.0, "width": 0.3554, "height": 1.0},
                )
            )
        ]
    )

    assert parsed.steps[0].id == "save"


def test_attributes_outside_the_allowlist_are_rejected() -> None:
    [(path, message)] = rejected(attributes={"class": "btn", "id": "save"})

    assert path == "steps[0].target.attributes.class"
    assert message.startswith("'class' is not a field of")
    assert "id, name, type, autocomplete, placeholder, aria_label, data_testid, href" in message


def test_a_close_misspelling_of_an_attribute_gets_a_suggestion() -> None:
    assert rejected(attributes={"data_testId": "save"}) == [
        (
            "steps[0].target.attributes.data_testId",
            "unknown field 'data_testId'; did you mean 'data_testid'?",
        )
    ]


@pytest.mark.parametrize(
    "href",
    [
        "https://portal.example.test/reports.html",
        "//portal.example.test/reports.html",
        "reports.html",
        "/reports.html?session=abc",
        "/reports.html#top",
    ],
)
def test_href_is_stored_as_a_path_only(href: str) -> None:
    assert rejected(attributes={"href": href}) == [
        (
            "steps[0].target.attributes.href",
            "must be a path only, starting with '/', with no host, query string, or fragment",
        )
    ]


@pytest.mark.parametrize(
    ("bbox", "message"),
    [
        ({"x": -0.1, "y": 0, "width": 0.1, "height": 0.1}, "must be at least 0.0"),
        ({"x": 0, "y": 1.5, "width": 0.1, "height": 0.1}, "must be at most 1.0"),
        ({"x": 0.9, "y": 0, "width": 0.2, "height": 0.1}, "x + width must not exceed 1"),
        ({"x": 0, "y": 0.5, "width": 0.1, "height": 0.6}, "y + height must not exceed 1"),
    ],
)
def test_bbox_stays_within_the_document(bbox: dict[str, float], message: str) -> None:
    [(_, reported)] = rejected(bbox=bbox)

    assert reported == message


@pytest.mark.parametrize(
    ("overrides", "path", "message"),
    [
        (
            {"tag": "Button"},
            "tag",
            "must be a lowercase HTML tag name, such as 'button' or 'my-widget'",
        ),
        ({"accessible_name": "x" * 257}, "accessible_name", "must be at most 256 characters"),
        ({"text": "x" * 1025}, "text", "must be at most 1024 characters"),
        ({"nearby_text": ["heading"] * 9}, "nearby_text", "must have at most 8 items"),
        (
            {"selectors": [{"strategy": "test_id", "value": str(n)} for n in range(11)]},
            "selectors",
            "must have at most 10 items",
        ),
        (
            {"structural_path": "main\n> button"},
            "structural_path",
            "must not contain control characters or line breaks (found U+000A)",
        ),
        (
            {"label_text": "Name\u2028Surname"},
            "label_text",
            "must not contain control characters or line breaks (found U+2028)",
        ),
    ],
)
def test_lengths_and_characters_are_bounded(
    overrides: dict[str, Any], path: str, message: str
) -> None:
    assert rejected(**overrides) == [(f"steps[0].target.{path}", message)]


def test_format_characters_such_as_zero_width_joiners_are_allowed() -> None:
    name = "Team \U0001f469\u200d\U0001f4bb"

    parsed = version(steps=[click_step(target=fingerprint(accessible_name=name))])

    target = step_target(parsed.steps[0])
    assert target is not None
    assert target.accessible_name == name
