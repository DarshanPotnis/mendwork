"""What the benchmark scorecard shows, decided from results documents alone (ADR 0014)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from mendwork.engine.benchmark.cells import UNRECORDED, CellResult, UsageMeasurement
from mendwork.engine.benchmark.metrics import LatencySummary, Ratio
from mendwork.engine.benchmark.results import (
    BenchmarkResults,
    ModelProvenance,
    Provenance,
    SkippedSection,
    SystemDescription,
    SystemKind,
    build_section,
)
from mendwork.engine.benchmark.truth import Resolution, StopKind
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.reporting.scorecard_view import (
    OUTCOME_GROUPS,
    Table,
    fraction,
    latency_text,
    scorecard_view,
)
from tests.unit.benchmark.builders import (
    abstained,
    case_cell,
    chaos_cell,
    direct_correct,
    failure,
    false_success,
    healed_correct,
    not_reached,
    unjudged,
    unnecessary,
)

SYSTEMS = ("css_selector", "ladder_free", "ladder_model")


def descriptions() -> tuple[SystemDescription, ...]:
    return (
        SystemDescription(
            id="css_selector",
            label="Recorded CSS selector (plain Playwright script)",
            short_label="CSS selector script",
            kind=SystemKind.SCRIPT,
            description="A script.",
        ),
        SystemDescription(
            id="ladder_free",
            label="Mendwork, free rungs only",
            short_label="Mendwork, free rungs",
            kind=SystemKind.LADDER,
            description="The product.",
            gated=True,
        ),
        SystemDescription(
            id="ladder_model",
            label="Mendwork + local model",
            short_label="Mendwork + model",
            kind=SystemKind.LADDER,
            description="The product with a model.",
            chooser="local model",
            gated=True,
        ),
    )


def provenance() -> Provenance:
    return Provenance(
        generated_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        mendwork_version="0.1.0",
        platform="macOS",
        python="3.12.14",
        browser="chromium 140",
        concurrency=4,
        step_timeout_ms=10_000,
        levels=(2, 5),
        seeds=(1000, 1001),
        workflows=("download_report",),
        source_digests={"src/mendwork": "sha256:abc"},
        settings_overrides={"trace_on_failure": False},
        models=(
            ModelProvenance(
                system="ladder_model",
                provider="ollama",
                model="local model",
                prompt_version="choose-candidate/1",
            ),
        ),
        notes=("A note.",),
    )


def grid_cells() -> tuple[CellResult, ...]:
    return (
        chaos_cell(
            [direct_correct("fill_email"), healed_correct("sign_in"), abstained("open_reports")],
            level=5,
            durations=[100, 900, 10_100],
        ),
        chaos_cell([unnecessary("sign_in")], level=2, run_status="failed"),
        chaos_cell([direct_correct("fill_email"), false_success("sign_in")], system="css_selector"),
        chaos_cell(
            [healed_correct("sign_in", rung=Resolution.RUNG_3)],
            system="ladder_model",
            model_calls=2,
            usage=UsageMeasurement(input_tokens=800, output_tokens=140, cost_usd=Decimal(0)),
        ),
    )


def grid_document() -> BenchmarkResults:
    return BenchmarkResults(
        provenance=provenance(),
        systems=descriptions(),
        sections=(build_section("chaos_grid", "Chaos grid", "Grid.", grid_cells(), SYSTEMS),),
        skipped=(SkippedSection(id="real_app", title="Real-app pairs", reason="no runtime"),),
    )


def table(caption_start: str, tables: tuple[Table, ...]) -> Table:
    return next(item for item in tables if item.caption.startswith(caption_start))


def test_the_headline_tiles_count_each_systems_wrong_steps_in_the_leading_section() -> None:
    view = scorecard_view([grid_document()])

    assert [(tile.label, tile.value) for tile in view.tiles] == [
        ("Wrong-action steps · CSS selector script", "1"),
        ("Wrong-action steps · Mendwork, free rungs", "0"),
        ("Wrong-action steps · Mendwork + model", "0"),
    ]
    assert view.tiles[0].detail == "of 2 steps reached in Chaos grid; 1 false success"


def test_each_comparison_chart_draws_every_system_with_mendwork_emphasized() -> None:
    section = scorecard_view([grid_document()]).sections[0]

    assert [chart.id for chart in section.charts] == [
        "chaos_grid-wrong",
        "chaos_grid-changed",
        "chaos_grid-abstain",
        "chaos_grid-unnecessary",
    ]
    wrong = section.charts[0]
    assert [(bar.label, bar.emphasis, bar.value, bar.text) for bar in wrong.bars] == [
        ("CSS selector script", False, 0.5, "1 of 2 (50.0%)"),
        ("Mendwork, free rungs", True, 0.0, "0 of 4 (0.0%)"),
        ("Mendwork + model", True, 0.0, "0 of 1 (0.0%)"),
    ]
    abstain = section.charts[2]
    assert [bar.value for bar in abstain.bars] == [None, 1.0, None]
    assert abstain.bars[0].text == "none reached"


def test_the_outcome_stack_keeps_the_validated_order_and_leaves_out_empty_groups() -> None:
    section = scorecard_view([grid_document()]).sections[0]
    free = section.stack[1]

    assert [segment.group for segment in free.segments] == [
        "resolved",
        "abstained_correct",
        "abstained_unnecessary",
    ]
    assert [segment.count for segment in free.segments] == [2, 1, 1]
    assert sum(segment.share for segment in free.segments) == pytest.approx(1.0)
    order = [group for group, _ in OUTCOME_GROUPS]
    assert [group for group, _ in section.groups] == [
        group
        for group in order
        if group in {"resolved", "abstained_correct", "abstained_unnecessary", "wrong"}
    ]


def test_the_summary_table_explains_heals_calls_and_costs_per_system() -> None:
    summary = table("Every system", scorecard_view([grid_document()]).sections[0].tables)

    rows = {row[0]: row for row in summary.rows}
    script = rows["Recorded CSS selector (plain Playwright script)"]
    free = rows["Mendwork, free rungs only"]
    model = rows["Mendwork + local model"]
    assert summary.header[0] == "System"
    assert (script[1], script[2], script[3], script[4], script[6]) == (
        "2 of 2",
        "1 of 2 (50.0%)",
        "1",
        "0 of 2 (0.0%)",
        "not applicable",
    )
    assert (free[6], free[9], free[10]) == ("1 of 2 (50.0%)", "0", "no model calls")
    assert (model[9], model[10]) == ("2.00", "$0.0000")


def test_a_call_without_a_price_is_never_shown_as_free() -> None:
    cells = (
        chaos_cell(
            [healed_correct()],
            system="ladder_model",
            model_calls=1,
            usage=UsageMeasurement(unpriced_calls=1),
        ),
    )
    document = BenchmarkResults(
        provenance=provenance(),
        systems=descriptions(),
        sections=(build_section("chaos_grid", "Chaos grid", "d", cells, SYSTEMS),),
    )

    summary = table("Every system", scorecard_view([document]).sections[0].tables)

    assert summary.rows[0][10] == "no price for 1 call"


def test_the_class_group_strength_rung_and_latency_tables_hold_every_number() -> None:
    tables = scorecard_view([grid_document()]).sections[0].tables

    classes = table("Steps in each outcome class", tables)
    free = next(row for row in classes.rows if row[0] == "Mendwork, free rungs only")
    assert classes.header[1:3] == ("Correct, recorded target", "Correct, healed")
    assert free[1:] == ("1", "1", "1", "1", "0", "0", "0", "0", "0", "0")

    groups = table("By level", tables)
    assert groups.header == (
        "Level",
        "CSS selector script",
        "Mendwork, free rungs",
        "Mendwork + model",
    )
    assert [row[0] for row in groups.rows] == ["level 2", "level 5"]
    assert groups.rows[0][1] == "-"
    assert groups.rows[0][2] == "0 wrong · 0/1 changed · 1 unnecessary"
    assert groups.rows[1][1] == "1 wrong (1 false success) · 0/0 changed · 0 unnecessary"

    strengths = table("By checkpoint strength", tables)
    assert [row[0] for row in strengths.rows] == ["strong", "weak", "none"]
    assert strengths.rows[2][1] == "-"

    rungs = table("Where correctly resolved", tables)
    model = next(row for row in rungs.rows if row[0] == "Mendwork + local model")
    free_rungs = next(row for row in rungs.rows if row[0] == "Mendwork, free rungs only")
    assert model[1:5] == ("0", "0", "0", "1")
    assert free_rungs[5] == "below_threshold (1), top_rejected (1)"

    latency = table("Step latency", tables)
    free_latency = next(row for row in latency.rows if row[0] == "Mendwork, free rungs only")
    assert free_latency[1] == "p50 100 ms · p95 10,100 ms (4)"


def test_provenance_skipped_suites_systems_and_method_are_carried_to_the_page() -> None:
    view = scorecard_view([grid_document()])

    facts = dict(view.provenance[0].facts)
    assert view.provenance[0].title == "Chaos grid"
    assert facts["Seeds"] == "2 (1000 to 1001)"
    assert facts["Levels"] == "2, 5"
    assert facts["Model"] == "ollama local model (choose-candidate/1)"
    assert facts["Setting"] == "trace_on_failure = False"
    assert facts["Note"] == "A note."
    assert view.provenance[0].digests == (("src/mendwork", "sha256:abc"),)
    assert view.skipped == (("Real-app pairs", "no runtime"),)
    first_system = next(label for label, _ in view.systems)
    assert first_system == "Recorded CSS selector (plain Playwright script)"
    assert view.methodology


def test_documents_are_shown_in_the_order_given_and_the_first_one_supplies_the_tiles() -> None:
    cases = (case_cell([direct_correct()], mutation="synonym_rename"),)
    leading = BenchmarkResults(
        provenance=provenance(),
        systems=descriptions(),
        sections=(build_section("single_mutations", "Single mutations", "d", cases, SYSTEMS),),
    )

    view = scorecard_view([leading, grid_document()])

    assert [section.id for section in view.sections] == ["single_mutations", "chaos_grid"]
    assert view.tiles[0].detail.endswith("in Single mutations; 0 false successes")
    assert table("By change", view.sections[0].tables).rows[0][0] == "synonym_rename"


def test_a_scorecard_needs_a_document() -> None:
    with pytest.raises(ValueError, match="at least one"):
        scorecard_view([])


def test_rates_and_latency_read_plainly_when_nothing_was_counted() -> None:
    assert fraction(Ratio(numerator=0, denominator=0)).text == "none reached"
    assert latency_text(LatencySummary(count=0)) == "-"


def unjudged_document() -> BenchmarkResults:
    cells = (chaos_cell([direct_correct("fill_email"), unjudged("sign_in")], system="ladder_free"),)
    return BenchmarkResults(
        provenance=provenance(),
        systems=descriptions(),
        sections=(build_section("real_app", "Pair", "A pair.", cells, SYSTEMS),),
    )


def test_steps_ground_truth_could_not_judge_get_their_own_segment_and_column() -> None:
    section = scorecard_view([unjudged_document()]).sections[0]

    stack = section.stack[0]
    assert [(segment.group, segment.count) for segment in stack.segments] == [
        ("resolved", 1),
        ("unjudged", 1),
    ]
    assert ("unjudged", "Ground truth unknown") in section.groups
    summary = table("Every system", section.tables)
    assert summary.header[4] == "Ground truth unknown"
    assert summary.rows[0][4] == "1 of 2 (50.0%)"
    classes = table("Steps in each outcome class", section.tables)
    assert classes.header[6] == "Ground truth unknown"
    assert classes.rows[0][6] == "1"


def test_the_methodology_explains_unjudged_steps_and_the_css_baselines_limit() -> None:
    view = scorecard_view([grid_document()])

    assert any("never counted wrong and never counted correct" in line for line in view.methodology)
    assert any(
        "property of our recorder, not of CSS selectors in general" in line
        for line in view.methodology
    )


def test_the_scorecard_says_why_runs_stopped_including_at_steps_with_no_target() -> None:
    """A failure the benchmark could not explain must be visible on the page, not absent from it."""
    cells = (
        chaos_cell(
            [not_reached()],
            seed=1000,
            run_status="failed",
            run_failure=failure(
                "open_portal",
                reason="NavigationFailed",
                stop=StopKind.ERROR,
                action=ActionType.NAVIGATE,
                targeted=False,
            ),
        ),
        chaos_cell(
            [not_reached()],
            seed=1001,
            run_status="failed",
            run_failure=failure("sign_in", reason=UNRECORDED, stop=StopKind.ERROR),
        ),
    )
    document = BenchmarkResults(
        provenance=provenance(),
        systems=descriptions(),
        sections=(build_section("chaos_grid", "Chaos grid", "d", cells, SYSTEMS),),
    )

    stops = table("Why runs stopped", scorecard_view([document]).sections[0].tables)

    assert stops.header == ("System", "Step", "Reason", "Runs")
    assert [row[1:] for row in stops.rows] == [
        ("open_portal (no target)", "NavigationFailed", "1"),
        ("sign_in", "unrecorded", "1"),
    ]
