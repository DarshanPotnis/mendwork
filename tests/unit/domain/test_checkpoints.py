"""Checkpoints: explicit URL modes, patterns compiled at load time, bounded timeouts."""

from typing import Any

import pytest
from pydantic import TypeAdapter

from mendwork.engine.domain.checkpoints import Checkpoint, ResponseReceived, UrlMatches
from tests.workflows import click_step, document, problems

CHECKPOINT: TypeAdapter[Checkpoint] = TypeAdapter(Checkpoint)


def rejected(checkpoint: dict[str, Any]) -> list[tuple[str, str]]:
    return problems(document(steps=[click_step(checkpoints=[checkpoint])]))


@pytest.mark.parametrize(
    "raw",
    [
        {
            "kind": "url_matches",
            "mode": "exact",
            "pattern": "https://portal.example.test/dashboard",
        },
        {"kind": "url_matches", "mode": "prefix", "pattern": "https://portal.example.test/app/"},
        {
            "kind": "url_matches",
            "mode": "regex",
            "pattern": r"https://[^/]+/orders/\d+",
            "timeout_ms": 5000,
        },
        {
            "kind": "element_visible",
            "selector": {"strategy": "role_name", "role": "heading", "name": "Orders"},
        },
        {"kind": "text_present", "text": "14 shipments"},
        {"kind": "download_completed", "filename_pattern": r"shipments_.*\.csv"},
        {
            "kind": "response_received",
            "mode": "prefix",
            "pattern": "https://api.example.test/",
            "status_min": 200,
            "status_max": 299,
        },
        {"kind": "no_error_banner"},
        {"kind": "no_error_banner", "selector": {"strategy": "css", "value": ".flash--error"}},
    ],
)
def test_every_checkpoint_kind_is_accepted(raw: dict[str, Any]) -> None:
    assert CHECKPOINT.validate_python(raw).kind == raw["kind"]


@pytest.mark.parametrize(
    ("mode", "pattern", "url", "matches"),
    [
        ("exact", "https://a.test/x", "https://a.test/x", True),
        ("exact", "https://a.test/x", "https://a.test/x?y=1", False),
        ("prefix", "https://a.test/app/", "https://a.test/app/orders", True),
        ("prefix", "https://a.test/app/", "https://a.test/other", False),
        ("regex", r"https://a\.test/orders", "https://a.test/orders", True),
        ("regex", r"https://a\.test/orders", "https://a.test/orders-deleted", False),
        ("regex", r"/orders", "https://a.test/orders", False),
    ],
)
def test_url_modes_compare_as_documented(mode: str, pattern: str, url: str, matches: bool) -> None:
    checkpoint = UrlMatches.model_validate(
        {"kind": "url_matches", "mode": mode, "pattern": pattern}
    )

    assert checkpoint.matches_url(url) is matches


@pytest.mark.parametrize(
    ("checkpoint", "path", "message"),
    [
        (
            {"kind": "url_matches", "mode": "regex", "pattern": "https://(unclosed"},
            ".pattern",
            "is not a valid regular expression: missing ), unterminated subpattern at position 8",
        ),
        (
            {"kind": "url_matches", "mode": "exact", "pattern": "/dashboard"},
            ".pattern",
            "must be an absolute http or https URL, such as https://example.com/ "
            "(mode is exact; use mode: regex for a pattern)",
        ),
        (
            {"kind": "url_matches", "mode": "glob", "pattern": "*"},
            ".mode",
            "must be one of: 'exact', 'prefix' or 'regex'",
        ),
        ({"kind": "url_matches", "pattern": "https://a.test/"}, ".mode", "is required"),
        (
            {"kind": "download_completed", "filename_pattern": "report[.csv"},
            ".filename_pattern",
            "is not a valid regular expression: unterminated character set at position 6",
        ),
        (
            {"kind": "text_present", "text": "x", "timeout_ms": 0},
            ".timeout_ms",
            "must be at least 1",
        ),
        (
            {"kind": "text_present", "text": "x", "timeout_ms": 600001},
            ".timeout_ms",
            "must be at most 600000",
        ),
        (
            {
                "kind": "response_received",
                "mode": "prefix",
                "pattern": "https://a.test/",
                "status_min": 500,
                "status_max": 200,
            },
            "",
            "status_min must not be greater than status_max",
        ),
        (
            {
                "kind": "response_received",
                "mode": "prefix",
                "pattern": "https://a.test/",
                "status_min": 99,
                "status_max": 200,
            },
            ".status_min",
            "must be at least 100",
        ),
        (
            {"kind": "no_error_banner", "timeout_ms": 1000},
            ".timeout_ms",
            "'timeout_ms' is not a field of 'no_error_banner'; allowed: kind, selector",
        ),
        (
            {"kind": "screenshot_matches"},
            "",
            "kind must be one of: url_matches, element_visible, text_present, "
            "download_completed, response_received, no_error_banner (got 'screenshot_matches')",
        ),
    ],
)
def test_checkpoint_mistakes_are_explained(
    checkpoint: dict[str, Any], path: str, message: str
) -> None:
    assert rejected(checkpoint) == [(f"steps[0].checkpoints[0]{path}", message)]


def test_duplicate_checkpoints_are_rejected() -> None:
    banner = {"kind": "no_error_banner"}

    assert rejected_many([banner, banner]) == [
        ("steps[0]", "checkpoints[1] duplicates checkpoints[0]")
    ]


def rejected_many(checkpoints: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return problems(document(steps=[click_step(checkpoints=checkpoints)]))


def test_a_timeout_is_optional_so_the_runtime_default_applies() -> None:
    checkpoint = ResponseReceived.model_validate(
        {
            "kind": "response_received",
            "mode": "exact",
            "pattern": "https://a.test/",
            "status_min": 200,
            "status_max": 200,
        }
    )

    assert checkpoint.timeout_ms is None
