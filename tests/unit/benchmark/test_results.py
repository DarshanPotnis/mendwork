"""The results document: round trips, the outcomes digest, integrity checks, and the schema."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from mendwork.engine.benchmark.cells import CellResult, StepTiming, UsageMeasurement
from mendwork.engine.benchmark.digest import DIGEST_PREFIX, outcomes_digest
from mendwork.engine.benchmark.outcomes import StepOutcome
from mendwork.engine.benchmark.results import (
    BenchmarkResults,
    Provenance,
    Section,
    SkippedSection,
    SystemDescription,
    SystemKind,
    build_section,
    results_json_schema,
    results_json_schema_text,
)
from tests.unit.benchmark.builders import (
    abstained,
    case_cell,
    chaos_cell,
    direct_correct,
    false_success,
    healed_correct,
    unnecessary,
)

REPO: Final = Path(__file__).resolve().parents[3]
SCHEMA_PATH: Final = REPO / "schemas" / "bench-results.schema.json"
SYSTEMS: Final = ("css_selector", "ladder_free")


def descriptions() -> tuple[SystemDescription, ...]:
    return (
        SystemDescription(
            id="css_selector",
            label="Recorded CSS selector",
            short_label="CSS selector script",
            kind=SystemKind.SCRIPT,
            description="A plain Playwright script using each step's recorded CSS selector.",
        ),
        SystemDescription(
            id="ladder_free",
            label="Mendwork, free rungs only",
            short_label="Mendwork, free rungs",
            kind=SystemKind.LADDER,
            description="The product as shipped, with no model configured.",
            gated=True,
        ),
    )


def provenance() -> Provenance:
    return Provenance(
        generated_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        mendwork_version="0.1.0",
        platform="macOS-26.5-arm64",
        python="3.12.14",
        browser="chromium 140.0",
        concurrency=4,
        step_timeout_ms=10_000,
        levels=(2, 3, 5),
        seeds=(1000, 1001),
        workflows=("download_report",),
        source_digests={"src": "sha256:" + "0" * 64},
        settings_overrides={"trace_on_failure": False},
    )


def cells() -> tuple[CellResult, ...]:
    return (
        chaos_cell([direct_correct(), healed_correct("sign_in")], seed=1000),
        chaos_cell([unnecessary()], seed=1001, run_status="failed"),
        chaos_cell([false_success()], system="css_selector", seed=1000),
    )


def document(section_cells: tuple[CellResult, ...] = ()) -> BenchmarkResults:
    chosen = section_cells or cells()
    return BenchmarkResults(
        provenance=provenance(),
        systems=descriptions(),
        sections=(
            build_section("chaos_grid", "Chaos grid", "Levels 2, 3, and 5.", chosen, SYSTEMS),
        ),
        skipped=(SkippedSection(id="real_app", title="Real-app pairs", reason="not run"),),
    )


def test_a_results_document_survives_a_json_round_trip() -> None:
    results = document()

    assert BenchmarkResults.model_validate_json(results.model_dump_json()) == results


def test_a_section_summarizes_its_systems_in_the_given_order_and_skips_absent_ones() -> None:
    section = build_section(
        "chaos_grid", "Chaos grid", "d", cells(), ("ladder_free", "role_name", "css_selector")
    )

    assert [summary.system for summary in section.systems] == ["ladder_free", "css_selector"]
    assert section.outcomes_digest.startswith(DIGEST_PREFIX)


def test_the_digest_ignores_measurements_and_order_but_not_outcomes() -> None:
    original = cells()
    measured_differently = tuple(
        cell.model_copy(
            update={
                "timings": tuple(
                    StepTiming(step_id=timing.step_id, duration_ms=9_999) for timing in cell.timings
                ),
                "usage": UsageMeasurement(input_tokens=5, cost_usd=Decimal("1")),
                "duration_ms": 42,
            }
        )
        for cell in original
    )
    changed = (original[0].model_copy(update={"steps": (abstained(),)}), *original[1:])

    assert outcomes_digest(original) == outcomes_digest(reversed(measured_differently))
    assert outcomes_digest(original) != outcomes_digest(changed)
    assert outcomes_digest(original) != outcomes_digest(
        (original[0].model_copy(update={"model_calls": 1}), *original[1:])
    )


def test_a_section_whose_cells_were_edited_after_it_was_written_is_refused() -> None:
    written = json.loads(document().model_dump_json())
    written["sections"][0]["cells"][0]["model_calls"] = 99

    with pytest.raises(ValidationError, match="digest does not match"):
        BenchmarkResults.model_validate(written)


def test_a_section_must_summarize_exactly_the_systems_its_cells_ran() -> None:
    section = build_section("chaos_grid", "Chaos grid", "d", cells(), SYSTEMS)

    with pytest.raises(ValidationError, match="exactly its systems"):
        Section.model_validate({**section.model_dump(), "systems": section.systems[:1]})


def test_every_system_is_described_once_and_section_ids_are_unique() -> None:
    results = document()
    fields = results.model_dump()

    with pytest.raises(ValidationError, match="without a description"):
        BenchmarkResults.model_validate({**fields, "systems": fields["systems"][1:]})
    with pytest.raises(ValidationError, match="described once"):
        BenchmarkResults.model_validate({**fields, "systems": fields["systems"] * 2})
    with pytest.raises(ValidationError, match="unique"):
        BenchmarkResults.model_validate(
            {**fields, "skipped": [{"id": "chaos_grid", "title": "t", "reason": "r"}]}
        )


def test_sections_and_systems_are_found_by_id() -> None:
    results = document()

    assert results.section("chaos_grid") is results.sections[0]
    assert results.section("real_app") is None
    assert results.system("ladder_free").gated


def test_the_committed_results_schema_is_current() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == results_json_schema_text(), (
        "schemas/bench-results.schema.json is stale; regenerate it with `make schema` and commit it"
    )


def test_a_results_document_validates_against_its_json_schema() -> None:
    mixed = (*cells(), case_cell([direct_correct()]))
    validator = Draft202012Validator(results_json_schema())

    assert list(validator.iter_errors(json.loads(document(mixed).model_dump_json()))) == []


MAKERS: Final[list[Callable[[], StepOutcome]]] = [
    direct_correct,
    healed_correct,
    unnecessary,
    abstained,
    false_success,
]
step_lists = st.lists(st.sampled_from(MAKERS).map(lambda make: make()), min_size=1, max_size=4)


@given(st.lists(st.tuples(step_lists, st.sampled_from(SYSTEMS)), min_size=1, max_size=5))
def test_any_results_document_round_trips_and_keeps_its_digest(
    specs: list[tuple[list[StepOutcome], str]],
) -> None:
    built = tuple(
        chaos_cell(steps, system=system, seed=1000 + number)
        for number, (steps, system) in enumerate(specs)
    )
    results = document(built)
    again = BenchmarkResults.model_validate_json(results.model_dump_json())

    assert again == results
    assert again.sections[0].outcomes_digest == outcomes_digest(built)
