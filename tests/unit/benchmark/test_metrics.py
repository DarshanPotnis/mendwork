"""Benchmark metrics: ratios with denominators, percentiles, latency, and per-system totals."""

from collections.abc import Callable
from decimal import Decimal
from random import Random

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from mendwork.engine.benchmark.cells import UNRECORDED, UsageMeasurement
from mendwork.engine.benchmark.metrics import (
    FailureCount,
    Ratio,
    ReasonCount,
    latency_breakdown,
    nearest_rank,
    resolution_share,
    run_failures,
    step_metrics,
    summarize_system,
)
from mendwork.engine.benchmark.outcomes import StepOutcome
from mendwork.engine.benchmark.truth import Resolution, StopKind
from mendwork.engine.domain.enums import ActionType
from tests.unit.benchmark.builders import (
    abstained,
    chaos_cell,
    direct_correct,
    failure,
    false_success,
    healed_correct,
    not_reached,
    unjudged,
    unnecessary,
)


def test_a_ratio_never_counts_more_than_its_denominator() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        Ratio(numerator=2, denominator=1)
    assert Ratio(numerator=0, denominator=0).value is None
    assert Ratio(numerator=1, denominator=4).value == 0.25


@pytest.mark.parametrize(
    ("values", "fraction", "expected"),
    [
        ([], 0.5, None),
        ([7], 0.95, 7),
        ([40, 10, 30, 20], 0.5, 20),
        ([40, 10, 30, 20], 0.95, 40),
        (list(range(1, 101)), 0.95, 95),
    ],
)
def test_percentiles_are_nearest_rank(
    values: list[int], fraction: float, expected: int | None
) -> None:
    assert nearest_rank(values, fraction) == expected


@pytest.mark.parametrize("fraction", [0.0, 1.5, -0.1])
def test_a_percentile_fraction_must_be_a_fraction(fraction: float) -> None:
    with pytest.raises(ValueError, match="fraction"):
        nearest_rank([1], fraction)


def mixed() -> list[StepOutcome]:
    return [
        direct_correct("fill_email"),
        direct_correct("fill_password", changes=["change_ids_classes"]),
        healed_correct("sign_in"),
        unnecessary("open_reports"),
        abstained("apply_filter"),
        false_success("download_csv"),
        not_reached("fill_date_to"),
    ]


def test_every_rate_counts_only_reached_steps_of_its_kind() -> None:
    metrics = step_metrics(mixed())

    assert (metrics.steps, metrics.reached) == (7, 6)
    assert metrics.wrong_action_rate == Ratio(numerator=1, denominator=6)
    assert (metrics.false_successes, metrics.caught_wrong, metrics.wrong_actions) == (1, 0, 1)
    # Changed act steps: fill_password (direct), sign_in (healed), open_reports (declined).
    assert metrics.changed_step_completion == Ratio(numerator=2, denominator=3)
    # The ladder ran on sign_in and open_reports among act steps; apply_filter expects abstention.
    assert metrics.heal_success == Ratio(numerator=1, denominator=2)
    assert metrics.correct_abstain == Ratio(numerator=1, denominator=1)
    # Reached act steps: everything but apply_filter.
    assert metrics.unnecessary_abstain == Ratio(numerator=1, denominator=5)
    assert metrics.counts.direct_correct == 2
    assert metrics.counts.not_reached == 1
    assert (metrics.resolutions.direct, metrics.resolutions.rung_2) == (2, 1)
    assert (metrics.strengths.strong, metrics.strengths.weak, metrics.strengths.none) == (5, 1, 0)
    assert metrics.actions_checked == 4
    assert metrics.stop_reasons == (
        ReasonCount(reason="below_threshold", count=1),
        ReasonCount(reason="top_rejected", count=1),
    )


def test_unjudged_steps_are_reported_on_their_own_and_never_as_wrong_or_correct() -> None:
    steps = [direct_correct("fill_email"), unjudged("sign_in"), false_success("open_reports")]

    metrics = step_metrics(steps)

    assert metrics.unjudged_rate == Ratio(numerator=1, denominator=3)
    assert metrics.unknown_actions == 1
    assert metrics.counts.ground_truth_unknown == 1
    # The unjudged step is in the denominators and in neither numerator.
    assert metrics.wrong_action_rate == Ratio(numerator=1, denominator=3)
    assert metrics.changed_step_completion == Ratio(numerator=0, denominator=0)
    assert metrics.unnecessary_abstain == Ratio(numerator=0, denominator=3)


def test_a_suite_whose_ground_truth_never_answered_cannot_look_clean() -> None:
    metrics = step_metrics([unjudged("fill_email"), unjudged("sign_in")])

    assert metrics.wrong_action_rate == Ratio(numerator=0, denominator=2)
    assert metrics.unjudged_rate == Ratio(numerator=2, denominator=2)


def test_resolution_share_keys_counts_by_resolution() -> None:
    share = resolution_share(step_metrics(mixed()).resolutions)

    assert share == {
        Resolution.DIRECT: 2,
        Resolution.RUNG_1: 0,
        Resolution.RUNG_2: 1,
        Resolution.RUNG_3: 0,
    }


def test_latency_is_split_by_how_steps_ended_and_skips_steps_without_a_duration() -> None:
    cell = chaos_cell(
        [direct_correct("fill_email"), healed_correct("sign_in"), unnecessary(), not_reached("x")],
        durations=[100, 900, 10_100, 50],
    )
    other = chaos_cell([direct_correct("fill_email")], durations=[None], seed=1001)

    latency = latency_breakdown([cell, other])

    assert (latency.all.count, latency.all.p50_ms, latency.all.p95_ms) == (3, 900, 10_100)
    assert (latency.direct.count, latency.direct.p50_ms) == (1, 100)
    assert (latency.healed.count, latency.healed.p95_ms) == (1, 900)
    assert (latency.stopped.count, latency.stopped.p50_ms) == (1, 10_100)


def test_a_system_summary_groups_by_level_and_strength_and_totals_its_runs() -> None:
    cells = [
        chaos_cell(
            mixed(),
            level=2,
            model_calls=2,
            usage=UsageMeasurement(
                input_tokens=400, output_tokens=70, latency_ms=3_000, cost_usd=Decimal("0.002")
            ),
        ),
        chaos_cell([direct_correct()], level=5, run_status="failed"),
    ]

    summary = summarize_system("ladder_free", cells)

    assert [group.group for group in summary.groups] == ["level 2", "level 5"]
    assert [group.group for group in summary.by_strength] == ["strong", "weak", "none"]
    assert summary.by_strength[1].metrics.reached == 1
    assert (summary.runs.runs, summary.runs.succeeded, summary.runs.model_calls) == (2, 1, 2)
    assert (summary.runs.input_tokens, summary.runs.output_tokens) == (400, 70)
    assert summary.runs.calls_per_run == 1.0
    assert summary.runs.cost_per_run_usd == Decimal("0.001")


def test_cost_per_run_is_unknown_when_any_call_had_no_price_or_nothing_ran() -> None:
    priced = summarize_system(
        "ladder_model",
        [
            chaos_cell(
                [direct_correct()],
                system="ladder_model",
                model_calls=1,
                usage=UsageMeasurement(unpriced_calls=1),
            )
        ],
    )
    empty = summarize_system("ladder_free", [])

    assert priced.runs.cost_per_run_usd is None
    assert (empty.runs.cost_per_run_usd, empty.runs.calls_per_run) == (None, None)


def test_a_summary_refuses_another_systems_cells() -> None:
    with pytest.raises(ValueError, match="must belong to it"):
        summarize_system("css_selector", [chaos_cell([direct_correct()])])


MAKERS: list[Callable[[], StepOutcome]] = [
    direct_correct,
    healed_correct,
    unnecessary,
    abstained,
    false_success,
    not_reached,
]
outcome_lists = st.lists(st.sampled_from(MAKERS).map(lambda make: make()), max_size=12)


@given(outcome_lists, st.randoms(use_true_random=False))
def test_metrics_do_not_depend_on_the_order_of_steps(
    outcomes: list[StepOutcome], random: Random
) -> None:
    shuffled = list(outcomes)
    random.shuffle(shuffled)

    assert step_metrics(outcomes) == step_metrics(shuffled)


@given(outcome_lists)
def test_a_wrong_step_never_lowers_the_wrong_action_count(outcomes: list[StepOutcome]) -> None:
    before = step_metrics(outcomes).wrong_action_rate
    after = step_metrics([*outcomes, false_success()]).wrong_action_rate

    assert after.numerator == before.numerator + 1
    assert after.denominator == before.denominator + 1


def test_why_runs_stopped_is_counted_per_step_and_reason_commonest_first() -> None:
    cells = [
        chaos_cell(
            [not_reached()],
            seed=s,
            run_status="failed",
            run_failure=failure(
                "open_portal",
                reason="NavigationFailed",
                stop=StopKind.ERROR,
                action=ActionType.NAVIGATE,
                targeted=False,
            ),
        )
        for s in (1000, 1001)
    ] + [
        chaos_cell([unnecessary()], seed=1002, run_status="failed"),
        chaos_cell([direct_correct()], seed=1003),
    ]

    counts = run_failures(cells)

    assert [(c.step_id, c.reason, c.count, c.targeted) for c in counts] == [
        ("open_portal", "NavigationFailed", 2, False),
        ("open_reports", "below_threshold", 1, True),
    ]


def test_a_failure_nobody_explained_is_counted_as_unrecorded_not_dropped() -> None:
    cells = [
        chaos_cell(
            [not_reached()],
            run_status="failed",
            run_failure=failure("open_portal", reason=UNRECORDED, stop=StopKind.ERROR),
        )
    ]

    assert run_failures(cells) == (
        FailureCount(step_id="open_portal", reason=UNRECORDED, count=1, targeted=True),
    )


def test_a_system_summary_reports_why_its_runs_stopped() -> None:
    summary = summarize_system(
        "ladder_free",
        [chaos_cell([unnecessary()], run_status="failed"), chaos_cell([direct_correct()])],
    )

    assert [(c.reason, c.count) for c in summary.failures] == [("below_threshold", 1)]
