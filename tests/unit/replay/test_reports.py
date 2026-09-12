"""Run evidence from errors and page observations: JSON-safe, scrubbed, and bounded."""

import pytest
from pydantic import SecretStr

from mendwork.engine.domain.runs import ErrorCategory, SelectorOutcome, TargetEvidence
from mendwork.engine.errors import BrowserUnavailable, MendworkError, TargetNotFound
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.replay.reports import (
    DETAIL_MAX_LENGTH,
    detail,
    error_report,
    identity_report,
    target_evidence,
    to_json_value,
)
from mendwork.engine.safety.redaction import REDACTED
from mendwork.engine.safety.secret_scrub import SecretScrubber

SECRET = "hunter2-report"


def scrubber() -> SecretScrubber:
    result = SecretScrubber()
    result.register(SecretStr(SECRET))
    return result


class Opaque:
    def __str__(self) -> str:
        return "opaque value"


def test_context_values_become_json() -> None:
    value = {1: ("a", {"b": [2.5, None, True]}), "set": frozenset({"only"}), "other": Opaque()}

    assert to_json_value(value) == {
        "1": ["a", {"b": [2.5, None, True]}],
        "set": ["only"],
        "other": "opaque value",
    }


def test_an_error_report_is_scrubbed_categorized_and_leaves_evidence_out() -> None:
    error = BrowserUnavailable(
        f"browser died holding {SECRET}",
        detail=(SECRET, 3),
        target={"selectors": []},
    )

    report = error_report(error, scrubber())

    assert (report.type, report.category) == ("BrowserUnavailable", ErrorCategory.INFRASTRUCTURE)
    assert report.message == f"browser died holding {REDACTED}"
    assert report.context == {"detail": [REDACTED, 3]}


def test_a_step_error_is_categorized_as_a_step_failure() -> None:
    assert error_report(TargetNotFound("gone"), scrubber()).category is ErrorCategory.STEP


def test_target_evidence_is_read_back_from_an_error() -> None:
    evidence = TargetEvidence.model_validate(
        {"selectors": [{"rank": 0, "strategy": "css", "level_counts": [0], "outcome": "none"}]}
    )
    error = TargetNotFound("gone", target=evidence.model_dump(mode="json"))

    read = target_evidence(error)

    assert read == evidence
    assert read is not None
    assert read.selectors[0].outcome is SelectorOutcome.NONE
    assert target_evidence(TargetNotFound("gone")) is None


def test_malformed_target_evidence_is_an_error_rather_than_silently_dropped() -> None:
    with pytest.raises(MendworkError, match="malformed target evidence"):
        target_evidence(TargetNotFound("gone", target={"selectors": "not a list"}))


def test_page_identities_are_scrubbed_including_optional_fields() -> None:
    identity = ElementIdentity(tag="input", input_type=None, role=None, name=f"Key {SECRET}")

    report = identity_report(identity, scrubber())

    assert (report.tag, report.input_type, report.role, report.name) == (
        "input",
        None,
        None,
        f"Key {REDACTED}",
    )


def test_details_are_scrubbed_before_they_are_shortened() -> None:
    long_text = SECRET + "x" * 400

    shortened = detail(long_text, scrubber())

    assert len(shortened) == DETAIL_MAX_LENGTH
    assert shortened.startswith(REDACTED)
    assert shortened.endswith("…")
    assert detail("short", scrubber()) == "short"
