"""Benchmark metrics: exact ratios with their denominators, never bare percentages (ADR 0014).

Denominators differ between systems because a run that stops early never reaches its later steps,
so every rate carries its numerator and denominator, and the reach is reported beside it.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from decimal import Decimal
from math import ceil
from typing import Self

from pydantic import Field, model_validator

from mendwork.engine.benchmark.cells import UNRECORDED, CellResult, SystemId, cell_group
from mendwork.engine.benchmark.outcomes import OutcomeClass, StepOutcome, WrongKind
from mendwork.engine.benchmark.truth import Expectation, Label, Resolution
from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import VerificationStrength


class Ratio(DomainModel):
    """A count out of a count."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)

    @model_validator(mode="after")
    def _within(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("a ratio's numerator cannot exceed its denominator")
        return self

    @property
    def value(self) -> float | None:
        """The fraction, or None when nothing was counted."""
        return self.numerator / self.denominator if self.denominator else None


class OutcomeCounts(DomainModel):
    """How many steps landed in each class."""

    healed_wrong: int = 0
    direct_wrong: int = 0
    abstained_correct: int = 0
    abstained_unnecessary: int = 0
    healed_correct: int = 0
    direct_correct: int = 0
    failed: int = 0
    approval_requested: int = 0
    ground_truth_unknown: int = 0
    not_reached: int = 0


class ResolutionCounts(DomainModel):
    """Where the elements of correctly resolved steps came from."""

    direct: int = 0
    rung_1: int = 0
    rung_2: int = 0
    rung_3: int = 0


class StrengthCounts(DomainModel):
    """Reached steps by how strongly their checkpoints prove the element (ADR 0010)."""

    strong: int = 0
    weak: int = 0
    none: int = 0


class ReasonCount(DomainModel):
    """How often a stop reason occurred."""

    reason: Label
    count: int = Field(ge=1)


class StepMetrics(DomainModel):
    """Every step metric over a set of classified steps."""

    steps: int = Field(ge=0)
    reached: int = Field(ge=0)
    counts: OutcomeCounts
    wrong_action_rate: Ratio
    """Wrong steps out of reached steps: the headline."""
    false_successes: int = Field(ge=0)
    caught_wrong: int = Field(ge=0)
    wrong_actions: int = Field(ge=0)
    unjudged_rate: Ratio
    """Steps with an action ground truth could not judge, out of reached steps.

    These are never counted wrong or correct, so they are reported on their own: a suite whose
    ground truth stops answering would otherwise look like a suite with nothing to report.
    """
    unknown_actions: int = Field(ge=0)
    actions_checked: int = Field(ge=0)
    changed_step_completion: Ratio
    """Correctly resolved steps out of reached act steps whose control a release changed."""
    heal_success: Ratio
    """Healed correctly out of reached act steps where the ladder ran; 0/0 for a script."""
    correct_abstain: Ratio
    unnecessary_abstain: Ratio
    resolutions: ResolutionCounts
    strengths: StrengthCounts
    stop_reasons: tuple[ReasonCount, ...] = ()


def step_metrics(outcomes: Sequence[StepOutcome]) -> StepMetrics:
    """The metrics of a set of steps, independent of their order."""
    reached = [outcome for outcome in outcomes if outcome.reached]
    act = [outcome for outcome in reached if outcome.expectation is Expectation.ACT]
    abstain = [outcome for outcome in reached if outcome.expectation is Expectation.ABSTAIN]
    classes = Counter(outcome.outcome.value for outcome in outcomes)
    changed = [outcome for outcome in act if outcome.target_changed]
    laddered = [outcome for outcome in act if outcome.ladder_ran]
    correct = [outcome for outcome in reached if outcome.outcome.correct_resolution]
    reasons = Counter(outcome.stop_reason for outcome in reached if outcome.stop_reason is not None)
    return StepMetrics(
        steps=len(outcomes),
        reached=len(reached),
        counts=OutcomeCounts(**classes),
        wrong_action_rate=_ratio(reached, lambda outcome: outcome.outcome.wrong),
        false_successes=sum(1 for outcome in reached if outcome.wrong is WrongKind.FALSE_SUCCESS),
        caught_wrong=sum(1 for outcome in reached if outcome.wrong is WrongKind.CAUGHT),
        wrong_actions=sum(outcome.wrong_actions for outcome in reached),
        unjudged_rate=_ratio(
            reached, lambda outcome: outcome.outcome is OutcomeClass.GROUND_TRUTH_UNKNOWN
        ),
        unknown_actions=sum(outcome.unknown_actions for outcome in reached),
        actions_checked=sum(outcome.actions for outcome in reached),
        changed_step_completion=_ratio(changed, lambda outcome: outcome.outcome.correct_resolution),
        heal_success=_ratio(
            laddered, lambda outcome: outcome.outcome is OutcomeClass.HEALED_CORRECT
        ),
        correct_abstain=_ratio(
            abstain, lambda outcome: outcome.outcome is OutcomeClass.ABSTAINED_CORRECT
        ),
        unnecessary_abstain=_ratio(
            act, lambda outcome: outcome.outcome is OutcomeClass.ABSTAINED_UNNECESSARY
        ),
        resolutions=ResolutionCounts(
            **Counter(outcome.resolution.value for outcome in correct if outcome.resolution)
        ),
        strengths=StrengthCounts(**Counter(outcome.strength.value for outcome in reached)),
        stop_reasons=tuple(
            ReasonCount(reason=reason, count=count)
            for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
        ),
    )


def _ratio(outcomes: Sequence[StepOutcome], counted: Callable[[StepOutcome], bool]) -> Ratio:
    return Ratio(
        numerator=sum(1 for outcome in outcomes if counted(outcome)), denominator=len(outcomes)
    )


def nearest_rank(values: Sequence[int], fraction: float) -> int | None:
    """The nearest-rank percentile: the smallest value with at least ``fraction`` at or below it."""
    if not 0 < fraction <= 1:
        raise ValueError("a percentile fraction is in (0, 1]")
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(fraction * len(ordered)) - 1)]


class LatencySummary(DomainModel):
    """Step durations at the 50th and 95th percentiles."""

    count: int = Field(ge=0)
    p50_ms: int | None = None
    p95_ms: int | None = None


def latency_summary(durations: Sequence[int]) -> LatencySummary:
    """The p50 and p95 of a set of durations."""
    return LatencySummary(
        count=len(durations),
        p50_ms=nearest_rank(durations, 0.5),
        p95_ms=nearest_rank(durations, 0.95),
    )


class LatencyBreakdown(DomainModel):
    """Step latency for all reached steps, and split by how the step ended."""

    all: LatencySummary
    direct: LatencySummary
    """Steps resolved correctly by the recorded selectors or a script's locator."""
    healed: LatencySummary
    stopped: LatencySummary
    """Steps that did not resolve correctly: abstentions, failures, wrong actions."""


def latency_breakdown(cells: Sequence[CellResult]) -> LatencyBreakdown:
    """Latency over every reached targeted step of the cells."""
    groups: dict[str, list[int]] = {"all": [], "direct": [], "healed": [], "stopped": []}
    for cell in cells:
        durations = {timing.step_id: timing.duration_ms for timing in cell.timings}
        for outcome in cell.steps:
            duration = durations.get(outcome.step_id)
            if not outcome.reached or duration is None:
                continue
            groups["all"].append(duration)
            if outcome.outcome is OutcomeClass.DIRECT_CORRECT:
                groups["direct"].append(duration)
            elif outcome.outcome is OutcomeClass.HEALED_CORRECT:
                groups["healed"].append(duration)
            else:
                groups["stopped"].append(duration)
    return LatencyBreakdown(**{name: latency_summary(values) for name, values in groups.items()})


class RunTotals(DomainModel):
    """Totals over a system's runs."""

    runs: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    model_latency_ms: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0)
    unpriced_calls: int = Field(ge=0)

    @property
    def calls_per_run(self) -> float | None:
        """Model calls per run, or None with no runs."""
        return self.model_calls / self.runs if self.runs else None

    @property
    def cost_per_run_usd(self) -> Decimal | None:
        """Estimated cost per run, or None when there were no runs or any call had no price."""
        if not self.runs or self.unpriced_calls:
            return None
        return self.cost_usd / self.runs


class GroupMetrics(DomainModel):
    """The metrics of one row: a level, a mutation, a pair, or a checkpoint strength."""

    group: Label
    metrics: StepMetrics


class FailureCount(DomainModel):
    """How often runs stopped at one kind of step for one reason."""

    step_id: Label
    reason: Label
    count: int = Field(ge=1)
    targeted: bool = True
    """False when the step acts on no control, such as a navigate: those have no outcome class."""


def run_failures(cells: Sequence[CellResult]) -> tuple[FailureCount, ...]:
    """Why every run that did not succeed stopped, commonest first.

    Nothing may vanish here: a cell whose failure carries no reason is counted under ``unrecorded``,
    so a suite that cannot explain itself says so on the page instead of looking clean.
    """
    counted: Counter[tuple[str, str, bool]] = Counter()
    for cell in cells:
        failure = cell.run_failure
        if failure is None:
            continue
        counted[(failure.step_id, failure.reason or UNRECORDED, failure.targeted)] += 1
    return tuple(
        FailureCount(step_id=step_id, reason=reason, count=count, targeted=targeted)
        for (step_id, reason, targeted), count in sorted(
            counted.items(), key=lambda item: (-item[1], item[0])
        )
    )


class SystemSummary(DomainModel):
    """Everything the scorecard shows for one system in one section."""

    system: SystemId
    overall: StepMetrics
    groups: tuple[GroupMetrics, ...]
    by_strength: tuple[GroupMetrics, ...]
    latency: LatencyBreakdown
    runs: RunTotals
    failures: tuple[FailureCount, ...] = ()
    """Why runs stopped, including at steps that act on no control."""


def summarize_system(system: str, cells: Sequence[CellResult]) -> SystemSummary:
    """The summary of one system's cells; cells of other systems are refused."""
    if any(cell.cell.system != system for cell in cells):
        raise ValueError(f"every cell summarized for {system} must belong to it")
    steps = [outcome for cell in cells for outcome in cell.steps]
    grouped: dict[str, list[StepOutcome]] = {}
    for cell in cells:
        grouped.setdefault(cell_group(cell.cell), []).extend(cell.steps)
    return SystemSummary(
        system=system,
        overall=step_metrics(steps),
        groups=tuple(
            GroupMetrics(group=group, metrics=step_metrics(items))
            for group, items in sorted(grouped.items())
        ),
        by_strength=tuple(
            GroupMetrics(
                group=strength.value,
                metrics=step_metrics(
                    [outcome for outcome in steps if outcome.strength is strength]
                ),
            )
            for strength in VerificationStrength
        ),
        latency=latency_breakdown(cells),
        failures=run_failures(cells),
        runs=RunTotals(
            runs=len(cells),
            succeeded=sum(1 for cell in cells if cell.run_status == "succeeded"),
            model_calls=sum(cell.model_calls for cell in cells),
            input_tokens=sum(cell.usage.input_tokens for cell in cells),
            output_tokens=sum(cell.usage.output_tokens for cell in cells),
            model_latency_ms=sum(cell.usage.latency_ms for cell in cells),
            cost_usd=sum((cell.usage.cost_usd for cell in cells), start=Decimal(0)),
            unpriced_calls=sum(cell.usage.unpriced_calls for cell in cells),
        ),
    )


def resolution_share(counts: ResolutionCounts) -> dict[Resolution, int]:
    """Resolution counts keyed by resolution, for charts."""
    return {
        Resolution.DIRECT: counts.direct,
        Resolution.RUNG_1: counts.rung_1,
        Resolution.RUNG_2: counts.rung_2,
        Resolution.RUNG_3: counts.rung_3,
    }
