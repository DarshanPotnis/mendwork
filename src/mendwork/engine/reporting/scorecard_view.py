"""What the benchmark scorecard shows, decided from results documents alone (ADR 0014).

Pure: every figure and sentence on the page is decided here, so the HTML adapter only lays it out
and a test can check each number without a browser. Documents are shown in the order given, and the
first one leads: its section supplies the headline tiles, so the suite with the broadest claim goes
first and a narrower one, such as a single real-application pair, follows in its own section.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from mendwork.engine.benchmark.cells import UNRECORDED
from mendwork.engine.benchmark.metrics import (
    GroupMetrics,
    LatencySummary,
    Ratio,
    StepMetrics,
    SystemSummary,
)
from mendwork.engine.benchmark.results import (
    BenchmarkResults,
    Section,
    SystemDescription,
    SystemKind,
)

TITLE: Final = "Mendwork benchmark scorecard"
OUTCOME_GROUPS: Final = (
    ("resolved", "Resolved correctly"),
    ("abstained_correct", "Abstained correctly"),
    ("abstained_unnecessary", "Abstained unnecessarily"),
    ("approval", "Stopped for approval"),
    ("unjudged", "Ground truth unknown"),
    ("failed", "Failed"),
    ("wrong", "Acted on a wrong element"),
)
"""In the order their colours were validated for neighbouring segments (ADR 0014)."""
CLASS_LABELS: Final = (
    ("direct_correct", "Correct, recorded target"),
    ("healed_correct", "Correct, healed"),
    ("abstained_correct", "Abstained correctly"),
    ("abstained_unnecessary", "Abstained unnecessarily"),
    ("approval_requested", "Stopped for approval"),
    ("ground_truth_unknown", "Ground truth unknown"),
    ("failed", "Failed"),
    ("direct_wrong", "Wrong, recorded target"),
    ("healed_wrong", "Wrong, healed"),
    ("not_reached", "Not reached"),
)
GROUP_HEADINGS: Final = {"chaos": "Level", "single_mutation": "Change", "real_app": "Pair"}
STRENGTHS: Final = ("strong", "weak", "none")
METHODOLOGY: Final = (
    "Every action is checked against ground truth at the moment it is sent. On the chaos portal "
    "that is the portal's own record of which element each control is, which Mendwork never "
    "reads; on a real application it is a person's labels, written before Mendwork ran on it.",
    "Each step gets one class, and a wrong action beats every other outcome: a step that acted on "
    "the wrong element and then recovered still acted on the wrong element.",
    "A false success is a wrong action whose checkpoints passed, so nothing in the run's record "
    "shows it; a caught wrong action is one whose checkpoints failed.",
    "An abstention is unnecessary when the real control was attached and visible when the system "
    "stopped, so a correct action was available and was declined.",
    "The script baselines check the workflow's own checkpoints, so they stop after a caught wrong "
    "action where an unasserted script would carry on; their wrong-action counts are lower bounds.",
    "The ground-truth chooser is not a model: it answers from ground truth, so its column is an "
    "upper bound on what Rung 3's rules allow.",
    "A step is ground truth unknown when ground truth could not name its control at the moment of "
    "an action, so the benchmark did not see what was reached. Such a step is never counted wrong "
    "and never counted correct; it is reported on its own, so an unanswerable measurement cannot "
    "pass for a clean one.",
    "The recorded CSS selector baseline can only run a step whose element the recorder gave a CSS "
    "selector, which it does only for elements with an id. A step it cannot express is reported as "
    "such, not as a failure to find the element: that limit is a property of our recorder, not of "
    "CSS selectors in general.",
    "Rates show their counts, because a system that stops early reaches fewer steps.",
)
Systems = Mapping[str, SystemDescription]


@dataclass(frozen=True, slots=True)
class Fraction:
    """A rate as a person reads it, and as a chart draws it."""

    text: str
    value: float | None


@dataclass(frozen=True, slots=True)
class Segment:
    """One outcome group's share of a system's reached steps."""

    group: str
    label: str
    count: int
    share: float


@dataclass(frozen=True, slots=True)
class Bar:
    """One system's value on a comparison chart."""

    label: str
    emphasis: bool
    """Whether the system is Mendwork's, drawn in the accent; baselines are drawn muted."""
    value: float | None
    text: str


@dataclass(frozen=True, slots=True)
class BarChart:
    """One metric compared across every system."""

    id: str
    title: str
    note: str
    bars: tuple[Bar, ...]


@dataclass(frozen=True, slots=True)
class StackRow:
    """One system's reached steps split into outcome groups."""

    label: str
    segments: tuple[Segment, ...]
    reached: int


@dataclass(frozen=True, slots=True)
class Table:
    """A table with a caption, a header row, and text cells."""

    caption: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Tile:
    """A headline number."""

    label: str
    value: str
    detail: str


@dataclass(frozen=True, slots=True)
class SectionView:
    """One suite: its charts, its outcome stack, and every table behind them."""

    id: str
    title: str
    description: str
    digest: str
    charts: tuple[BarChart, ...]
    stack: tuple[StackRow, ...]
    groups: tuple[tuple[str, str], ...]
    """The outcome groups present in the stack, in drawing order: the stack's legend."""
    tables: tuple[Table, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceView:
    """What one results document measured, where, and with what."""

    title: str
    facts: tuple[tuple[str, str], ...]
    digests: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ScorecardView:
    """The whole scorecard."""

    title: str
    tiles: tuple[Tile, ...]
    sections: tuple[SectionView, ...]
    skipped: tuple[tuple[str, str], ...]
    systems: tuple[tuple[str, str], ...]
    methodology: tuple[str, ...]
    provenance: tuple[ProvenanceView, ...]


@dataclass(frozen=True, slots=True)
class _Metric:
    key: str
    title: str
    note: str
    pick: Callable[[StepMetrics], Ratio]


METRICS: Final = (
    _Metric(
        "wrong",
        "Wrong-action rate",
        "Steps that acted on a wrong element. Lower is better; the target is 0.",
        lambda metrics: metrics.wrong_action_rate,
    ),
    _Metric(
        "changed",
        "Changed steps completed",
        "Steps whose control a release changed, completed on the right element. Higher is better.",
        lambda metrics: metrics.changed_step_completion,
    ),
    _Metric(
        "abstain",
        "Correct abstentions",
        "Steps that must not act, where the system acted on nothing. Higher is better.",
        lambda metrics: metrics.correct_abstain,
    ),
    _Metric(
        "unnecessary",
        "Unnecessary abstentions",
        "Steps whose real control was there, where the system stopped anyway. Lower is better.",
        lambda metrics: metrics.unnecessary_abstain,
    ),
)


def fraction(ratio: Ratio) -> Fraction:
    """A ratio as ``n of N (x%)``."""
    if ratio.denominator == 0:
        return Fraction("none reached", None)
    share = ratio.numerator / ratio.denominator
    return Fraction(f"{ratio.numerator:,} of {ratio.denominator:,} ({share:.1%})", share)


def outcome_counts(metrics: StepMetrics) -> dict[str, int]:
    """Reached steps per outcome group."""
    counts = metrics.counts
    return {
        "resolved": counts.direct_correct + counts.healed_correct,
        "abstained_correct": counts.abstained_correct,
        "abstained_unnecessary": counts.abstained_unnecessary,
        "approval": counts.approval_requested,
        "unjudged": counts.ground_truth_unknown,
        "failed": counts.failed,
        "wrong": counts.direct_wrong + counts.healed_wrong,
    }


def latency_text(summary: LatencySummary) -> str:
    """``p50 · p95 (n)``, or a dash when no step was timed."""
    if summary.p50_ms is None or summary.p95_ms is None:
        return "-"
    return f"p50 {summary.p50_ms:,} ms · p95 {summary.p95_ms:,} ms ({summary.count:,})"


def scorecard_view(documents: Sequence[BenchmarkResults]) -> ScorecardView:
    """The scorecard for one or more results documents, in the order given."""
    if not documents:
        raise ValueError("a scorecard needs at least one results document")
    systems: dict[str, SystemDescription] = {}
    for document in documents:
        for system in document.systems:
            systems.setdefault(system.id, system)
    sections = [section for document in documents for section in document.sections]
    return ScorecardView(
        title=TITLE,
        tiles=_tiles(sections[0], systems) if sections else (),
        sections=tuple(_section(section, systems) for section in sections),
        skipped=tuple(
            (item.title, item.reason) for document in documents for item in document.skipped
        ),
        systems=tuple((system.label, system.description) for system in systems.values()),
        methodology=METHODOLOGY,
        provenance=tuple(_provenance(document) for document in documents),
    )


def counted(count: int, singular: str, plural: str | None = None) -> str:
    """``1 false success``, ``2 false successes``: a count and its noun, agreeing."""
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count:,} {noun}"


def _tiles(section: Section, systems: Systems) -> tuple[Tile, ...]:
    return tuple(
        Tile(
            label=f"Wrong-action steps · {systems[summary.system].short_label}",
            value=f"{outcome_counts(summary.overall)['wrong']:,}",
            detail=(
                f"of {counted(summary.overall.reached, 'step')} reached in {section.title}; "
                f"{counted(summary.overall.false_successes, 'false success', 'false successes')}"
            ),
        )
        for summary in section.systems
    )


def _section(section: Section, systems: Systems) -> SectionView:
    summaries = section.systems
    stack = tuple(_stack(summary, systems) for summary in summaries)
    present = {segment.group for row in stack for segment in row.segments}
    return SectionView(
        id=section.id,
        title=section.title,
        description=section.description,
        digest=section.outcomes_digest,
        charts=tuple(_chart(section.id, metric, summaries, systems) for metric in METRICS),
        stack=stack,
        groups=tuple((group, label) for group, label in OUTCOME_GROUPS if group in present),
        tables=(
            _summary_table(summaries, systems),
            _class_table(summaries, systems),
            _group_table(section, systems),
            _strength_table(summaries, systems),
            _rung_table(summaries, systems),
            _latency_table(summaries, systems),
            _failure_table(summaries, systems),
        ),
    )


def _chart(
    section_id: str, metric: _Metric, summaries: Sequence[SystemSummary], systems: Systems
) -> BarChart:
    bars = []
    for summary in summaries:
        rate = fraction(metric.pick(summary.overall))
        description = systems[summary.system]
        bars.append(
            Bar(
                label=description.short_label,
                emphasis=description.kind is SystemKind.LADDER,
                value=rate.value,
                text=rate.text,
            )
        )
    return BarChart(f"{section_id}-{metric.key}", metric.title, metric.note, tuple(bars))


def _stack(summary: SystemSummary, systems: Systems) -> StackRow:
    counts = outcome_counts(summary.overall)
    reached = summary.overall.reached
    return StackRow(
        label=systems[summary.system].short_label,
        segments=tuple(
            Segment(group, label, counts[group], counts[group] / reached if reached else 0.0)
            for group, label in OUTCOME_GROUPS
            if counts[group]
        ),
        reached=reached,
    )


def _summary_table(summaries: Sequence[SystemSummary], systems: Systems) -> Table:
    rows = []
    for summary in summaries:
        metrics = summary.overall
        script = systems[summary.system].kind is SystemKind.SCRIPT
        calls = summary.runs.calls_per_run
        rows.append(
            (
                systems[summary.system].label,
                f"{metrics.reached:,} of {metrics.steps:,}",
                fraction(metrics.wrong_action_rate).text,
                f"{metrics.false_successes:,}",
                fraction(metrics.unjudged_rate).text,
                fraction(metrics.changed_step_completion).text,
                "not applicable" if script else fraction(metrics.heal_success).text,
                fraction(metrics.correct_abstain).text,
                fraction(metrics.unnecessary_abstain).text,
                "0" if not calls else f"{calls:.2f}",
                _cost(summary),
            )
        )
    header = (
        "System",
        "Steps reached",
        "Wrong-action steps",
        "False successes",
        "Ground truth unknown",
        "Changed steps completed",
        "Heal success",
        "Correct abstentions",
        "Unnecessary abstentions",
        "Model calls per run",
        "Estimated cost per run",
    )
    return Table("Every system on the same cells", header, tuple(rows))


def _class_table(summaries: Sequence[SystemSummary], systems: Systems) -> Table:
    return Table(
        "Steps in each outcome class (the table behind the outcome chart)",
        ("System", *(label for _, label in CLASS_LABELS)),
        tuple(
            (
                systems[summary.system].label,
                *(f"{getattr(summary.overall.counts, name):,}" for name, _ in CLASS_LABELS),
            )
            for summary in summaries
        ),
    )


def _group_table(section: Section, systems: Systems) -> Table:
    heading = GROUP_HEADINGS[section.cells[0].cell.type] if section.cells else "Group"
    names = sorted({group.group for summary in section.systems for group in summary.groups})
    rows = tuple(
        (
            name,
            *(
                _brief(next((group for group in summary.groups if group.group == name), None))
                for summary in section.systems
            ),
        )
        for name in names
    )
    header = (heading, *(systems[summary.system].short_label for summary in section.systems))
    return Table(f"By {heading.lower()}", header, rows)


def _strength_table(summaries: Sequence[SystemSummary], systems: Systems) -> Table:
    rows = tuple(
        (
            name,
            *(
                _brief(next((group for group in summary.by_strength if group.group == name), None))
                for summary in summaries
            ),
        )
        for name in STRENGTHS
    )
    return Table(
        "By checkpoint strength (strong checks prove the element; weak ones only where it led)",
        ("Checkpoints", *(systems[summary.system].short_label for summary in summaries)),
        rows,
    )


def _rung_table(summaries: Sequence[SystemSummary], systems: Systems) -> Table:
    rows = []
    for summary in summaries:
        resolutions = summary.overall.resolutions
        reasons = ", ".join(
            f"{item.reason} ({item.count:,})" for item in summary.overall.stop_reasons[:3]
        )
        rows.append(
            (
                systems[summary.system].label,
                f"{resolutions.direct:,}",
                f"{resolutions.rung_1:,}",
                f"{resolutions.rung_2:,}",
                f"{resolutions.rung_3:,}",
                reasons or "-",
            )
        )
    return Table(
        "Where correctly resolved elements came from, and the most common stops",
        ("System", "Recorded target", "Rung 1", "Rung 2", "Rung 3", "Most common stops"),
        tuple(rows),
    )


def _failure_table(summaries: Sequence[SystemSummary], systems: Systems) -> Table:
    """Why runs stopped, so a failure the results cannot explain is visible rather than absent."""
    rows = []
    for summary in summaries:
        for failure in summary.failures:
            where = failure.step_id if failure.targeted else f"{failure.step_id} (no target)"
            reason = failure.reason
            rows.append(
                (
                    systems[summary.system].label,
                    where,
                    "unrecorded" if reason == UNRECORDED else reason,
                    f"{failure.count:,}",
                )
            )
    return Table(
        "Why runs stopped. A step with no target acts on no control, so it has no outcome class; a "
        "reason shown as unrecorded is a failure this benchmark could not explain",
        ("System", "Step", "Reason", "Runs"),
        tuple(rows),
    )


def _latency_table(summaries: Sequence[SystemSummary], systems: Systems) -> Table:
    return Table(
        "Step latency. Mendwork's includes settling, identity checks, and evidence capture and a "
        "script's does not, so compare within a kind of system",
        ("System", "All steps", "Recorded target", "Healed", "Stopped or wrong"),
        tuple(
            (
                systems[summary.system].label,
                latency_text(summary.latency.all),
                latency_text(summary.latency.direct),
                latency_text(summary.latency.healed),
                latency_text(summary.latency.stopped),
            )
            for summary in summaries
        ),
    )


def _brief(group: GroupMetrics | None) -> str:
    if group is None or group.metrics.reached == 0:
        return "-"
    metrics = group.metrics
    wrong = f"{outcome_counts(metrics)['wrong']} wrong"
    if metrics.false_successes:
        wrong += f" ({counted(metrics.false_successes, 'false success', 'false successes')})"
    changed = metrics.changed_step_completion
    parts = [wrong, f"{changed.numerator}/{changed.denominator} changed"]
    if metrics.correct_abstain.denominator:
        abstain = metrics.correct_abstain
        parts.append(f"{abstain.numerator}/{abstain.denominator} abstained")
    parts.append(f"{metrics.counts.abstained_unnecessary} unnecessary")
    return " · ".join(parts)


def _cost(summary: SystemSummary) -> str:
    runs = summary.runs
    if runs.model_calls == 0:
        return "no model calls"
    per_run = runs.cost_per_run_usd
    if per_run is None:
        return f"no price for {counted(runs.unpriced_calls, 'call')}"
    return f"${per_run:.4f}"


def _provenance(document: BenchmarkResults) -> ProvenanceView:
    provenance = document.provenance
    facts = [
        ("Generated", provenance.generated_at.isoformat()),
        ("Mendwork", provenance.mendwork_version),
        ("Platform", provenance.platform),
        ("Python", provenance.python),
        ("Browser", provenance.browser),
        ("Runs at once", str(provenance.concurrency)),
        ("Step timeout", f"{provenance.step_timeout_ms:,} ms"),
    ]
    if provenance.levels:
        facts.append(("Levels", ", ".join(str(level) for level in provenance.levels)))
    if provenance.seeds:
        seeds = provenance.seeds
        facts.append(("Seeds", f"{len(seeds)} ({seeds[0]} to {seeds[-1]})"))
    if provenance.workflows:
        facts.append(("Workflows", ", ".join(provenance.workflows)))
    facts.extend(
        ("Model", f"{model.provider} {model.model} ({model.prompt_version})")
        for model in provenance.models
    )
    facts.extend(
        ("Setting", f"{name} = {value}") for name, value in provenance.settings_overrides.items()
    )
    facts.extend(("Note", note) for note in provenance.notes)
    title = ", ".join(section.title for section in document.sections) or "Results"
    return ProvenanceView(title, tuple(facts), tuple(sorted(provenance.source_digests.items())))
