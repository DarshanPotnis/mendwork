"""The benchmark gate: a gated system that acts on a wrong element fails the benchmark."""

from datetime import UTC, datetime

from mendwork.engine.benchmark.results import (
    BenchmarkResults,
    Provenance,
    SystemDescription,
    SystemKind,
    build_section,
    gate_failures,
)
from tests.unit.benchmark.builders import chaos_cell, direct_correct, false_success

SYSTEMS = ("css_selector", "ladder_free")


def results(*, ladder_wrong: bool, script_wrong: bool) -> BenchmarkResults:
    cells = (
        chaos_cell([false_success() if ladder_wrong else direct_correct()]),
        chaos_cell([false_success() if script_wrong else direct_correct()], system="css_selector"),
    )
    return BenchmarkResults(
        provenance=Provenance(
            generated_at=datetime(2026, 9, 15, tzinfo=UTC),
            mendwork_version="0.1.0",
            platform="p",
            python="3.12",
            browser="chromium",
            concurrency=1,
            step_timeout_ms=1_500,
        ),
        systems=(
            SystemDescription(
                id="css_selector",
                label="CSS",
                short_label="CSS",
                kind=SystemKind.SCRIPT,
                description="d",
            ),
            SystemDescription(
                id="ladder_free",
                label="Mendwork",
                short_label="Mendwork",
                kind=SystemKind.LADDER,
                description="d",
                gated=True,
            ),
        ),
        sections=(build_section("chaos_grid", "Chaos grid", "d", cells, SYSTEMS),),
    )


def test_a_gated_system_acting_on_a_wrong_element_fails_the_gate_with_its_numbers() -> None:
    assert gate_failures(results(ladder_wrong=True, script_wrong=False)) == (
        "chaos_grid: ladder_free acted on a wrong element at 1 of 1 steps (1 false success)",
    )


def test_a_script_baseline_acting_wrongly_does_not_fail_the_gate() -> None:
    assert gate_failures(results(ladder_wrong=False, script_wrong=True)) == ()
