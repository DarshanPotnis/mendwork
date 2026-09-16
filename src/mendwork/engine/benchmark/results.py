"""The benchmark's results document, version 1 (ADR 0014).

One JSON file holds what was measured, on what, by which systems, and every cell's outcome. A
section's digest is checked against its cells whenever the document is read, so a results file
edited by hand, or cut short, is refused rather than published.
"""

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import AwareDatetime, Field, JsonValue, StringConstraints, model_validator

from mendwork.engine.benchmark.cells import CellResult, SystemId
from mendwork.engine.benchmark.digest import outcomes_digest
from mendwork.engine.benchmark.metrics import SystemSummary, summarize_system
from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.json_schema import JSON_SCHEMA_DIALECT

RESULTS_SCHEMA_VERSION: Final = 1
Text = Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
SectionId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class SystemKind(StrEnum):
    """Whether a system is a plain script or Mendwork's ladder."""

    SCRIPT = "script"
    LADDER = "ladder"


class SystemDescription(DomainModel):
    """A benchmarked system, as the scorecard names it."""

    id: SystemId
    label: Text
    short_label: Annotated[str, StringConstraints(min_length=1, max_length=28)]
    """The name a chart row has room for: longer would run into the plot."""
    kind: SystemKind
    description: Text
    chooser: Text | None = None
    """Who chooses at Rung 3: a model, the ground-truth upper bound, or None without Rung 3."""
    gated: bool = False
    """Whether a wrong action by this system fails the benchmark."""


class Section(DomainModel):
    """One suite's cells, with every system's summary and the outcomes digest."""

    id: SectionId
    title: Text
    description: Text
    cells: tuple[CellResult, ...]
    systems: tuple[SystemSummary, ...]
    outcomes_digest: Text

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.outcomes_digest != outcomes_digest(self.cells):
            raise ValueError(f"section {self.id}: the outcomes digest does not match its cells")
        present = {cell.cell.system for cell in self.cells}
        if {summary.system for summary in self.systems} != present:
            raise ValueError(f"section {self.id}: summaries must cover exactly its systems")
        return self


def build_section(
    id: str, title: str, description: str, cells: tuple[CellResult, ...], systems: tuple[str, ...]
) -> Section:
    """A section with its summaries, in the given system order, and its digest."""
    present = [system for system in systems if any(cell.cell.system == system for cell in cells)]
    return Section(
        id=id,
        title=title,
        description=description,
        cells=cells,
        systems=tuple(
            summarize_system(system, [cell for cell in cells if cell.cell.system == system])
            for system in present
        ),
        outcomes_digest=outcomes_digest(cells),
    )


class SkippedSection(DomainModel):
    """A suite that was not run, and why."""

    id: SectionId
    title: Text
    reason: Text


class ModelProvenance(DomainModel):
    """The model behind a system's Rung 3."""

    system: SystemId
    provider: Text
    model: Text
    prompt_version: Text


class Provenance(DomainModel):
    """What was measured, where, and with what."""

    generated_at: AwareDatetime
    mendwork_version: Text
    platform: Text
    python: Text
    browser: Text
    concurrency: int = Field(ge=1)
    step_timeout_ms: int = Field(ge=1)
    levels: tuple[int, ...] = ()
    seeds: tuple[int, ...] = ()
    workflows: tuple[Text, ...] = ()
    source_digests: Mapping[str, Text] = Field(default_factory=dict)
    settings_overrides: Mapping[str, JsonValue] = Field(default_factory=dict)
    models: tuple[ModelProvenance, ...] = ()
    notes: tuple[Text, ...] = ()


class BenchmarkResults(DomainModel):
    """The whole results document."""

    schema_version: Literal[1] = RESULTS_SCHEMA_VERSION
    provenance: Provenance
    systems: tuple[SystemDescription, ...]
    sections: tuple[Section, ...]
    skipped: tuple[SkippedSection, ...] = ()

    @model_validator(mode="after")
    def _described(self) -> Self:
        described = [system.id for system in self.systems]
        if len(set(described)) != len(described):
            raise ValueError("each system is described once")
        used = {summary.system for section in self.sections for summary in section.systems}
        if not used <= set(described):
            raise ValueError(f"systems without a description: {sorted(used - set(described))}")
        ids = [section.id for section in self.sections] + [item.id for item in self.skipped]
        if len(set(ids)) != len(ids):
            raise ValueError("section ids are unique")
        return self

    def section(self, id: str) -> Section | None:
        """The section with this id, if it ran."""
        return next((section for section in self.sections if section.id == id), None)

    def system(self, id: str) -> SystemDescription:
        """The description of a system."""
        return next(system for system in self.systems if system.id == id)


def gate_failures(results: BenchmarkResults) -> tuple[str, ...]:
    """One line for every gated system that acted on a wrong element in any section.

    Only Mendwork's systems are gated: the script baselines are expected to act wrongly, which is
    what they are there to show.
    """
    gated = {system.id for system in results.systems if system.gated}
    failures: list[str] = []
    for section in results.sections:
        for summary in section.systems:
            metrics = summary.overall
            wrong = metrics.counts.healed_wrong + metrics.counts.direct_wrong
            if summary.system in gated and wrong:
                failures.append(
                    f"{section.id}: {summary.system} acted on a wrong element at {wrong} of "
                    f"{metrics.reached} steps ({metrics.false_successes} false "
                    f"{'success' if metrics.false_successes == 1 else 'successes'})"
                )
    return tuple(failures)


def results_json_schema() -> dict[str, JsonValue]:
    """The results schema as data."""
    schema = BenchmarkResults.model_json_schema(mode="validation")
    return {"$schema": JSON_SCHEMA_DIALECT, **schema, "title": "Mendwork benchmark results"}


def results_json_schema_text() -> str:
    """The schema exactly as committed to schemas/bench-results.schema.json."""
    return json.dumps(results_json_schema(), indent=2, ensure_ascii=False) + "\n"
