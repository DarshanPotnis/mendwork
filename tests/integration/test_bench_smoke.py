"""The CI smoke benchmark: Mendwork acts on no wrong element, and the published numbers are current.

It reruns the first cells of the published grid (bench_seeds.json: level 5, the first five seeds,
both example workflows) through Mendwork's free ladder and its ground-truth chooser, with every
action checked against the portal's ground truth (ADR 0014).

- **The gate.** Any wrong action or false success by either system fails the build.
- **Reproducibility.** The same cells in the committed ``benchmarks/results/chaos-results.json`` ran
  at the default 10 s step timeout; these run at 1.5 s, because a stopped step spends its time
  waiting for a control that never appears. Their outcomes digest must be identical, which shows the
  short timeout changes no outcome, that the seeds reproduce, and that the published numbers were
  made by this code. A change that alters these outcomes needs ``make bench`` run again.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
import pytest_asyncio

from benchmarks.chaos.bench import (
    CHAOS_GRID,
    ChaosBenchPlan,
    load_bench_workflows,
    run_chaos_benchmark,
)
from benchmarks.chaos.bench_seeds import load_bench_seeds
from mendwork.engine.benchmark.cells import CellResult, ChaosCell
from mendwork.engine.benchmark.digest import outcomes_digest
from mendwork.engine.benchmark.results import BenchmarkResults, gate_failures
from mendwork.settings import Settings

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

REPO: Final = Path(__file__).resolve().parents[2]
COMMITTED: Final = REPO / "benchmarks" / "results" / "chaos-results.json"
PLAN: Final = load_bench_seeds()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def smoke() -> BenchmarkResults:
    settings = Settings(_env_file=None).model_copy(
        update={"trace_on_failure": False, "step_timeout_ms": PLAN.smoke.step_timeout_ms}
    )
    plan = ChaosBenchPlan(
        workflows=load_bench_workflows(
            REPO / "workflows" / "examples", max_bytes=settings.workflow_max_bytes
        ),
        levels=(PLAN.smoke.level,),
        seeds=PLAN.smoke_seeds,
        systems=PLAN.smoke.systems,
        concurrency=4,
        single_mutations=False,
    )
    return await run_chaos_benchmark(
        plan, settings, generated_at=datetime(2026, 9, 15, tzinfo=UTC), repository=REPO
    )


def smoke_cells(results: BenchmarkResults) -> tuple[CellResult, ...]:
    section = results.section(CHAOS_GRID)
    assert section is not None
    return tuple(
        cell
        for cell in section.cells
        if isinstance(cell.cell, ChaosCell)
        and cell.cell.level == PLAN.smoke.level
        and cell.cell.seed in PLAN.smoke_seeds
        and cell.cell.system in PLAN.smoke.systems
    )


async def test_no_mendwork_system_acts_on_a_wrong_element(smoke: BenchmarkResults) -> None:
    section = smoke.section(CHAOS_GRID)
    assert section is not None

    assert gate_failures(smoke) == ()
    for summary in section.systems:
        assert summary.overall.false_successes == 0, summary.system
        assert summary.overall.wrong_actions == 0, summary.system
        assert summary.overall.actions_checked > 0, summary.system
    assert len(section.cells) == 2 * len(PLAN.smoke_seeds) * len(PLAN.smoke.systems)


async def test_the_smoke_cells_reproduce_the_committed_results(smoke: BenchmarkResults) -> None:
    committed = BenchmarkResults.model_validate_json(COMMITTED.read_bytes())
    ran = smoke_cells(smoke)
    published = smoke_cells(committed)
    published_by_cell = {cell.cell: cell for cell in published}
    differing = [
        cell.cell
        for cell in ran
        if outcomes_digest([cell])
        != outcomes_digest([published_by_cell[cell.cell]] if cell.cell in published_by_cell else [])
    ]

    assert len(published) == len(ran), "the committed results lack some smoke cells: run make bench"
    assert differing == [], (
        "these cells' outcomes differ from the committed results: run make bench"
    )
    assert outcomes_digest(ran) == outcomes_digest(published)


async def test_the_smoke_results_are_a_valid_results_document(smoke: BenchmarkResults) -> None:
    assert BenchmarkResults.model_validate_json(smoke.model_dump_json()) == smoke
