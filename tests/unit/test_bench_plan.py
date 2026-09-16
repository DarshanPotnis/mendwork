"""``make bench`` runs exactly the seeds and levels fixed in bench_seeds.json (ADR 0014)."""

import re
from pathlib import Path
from typing import Final

import pytest

from benchmarks.chaos.bench_seeds import BENCH_SEEDS_PATH, load_bench_seeds
from benchmarks.chaos.systems import SYSTEM_IDS

REPO: Final = Path(__file__).resolve().parents[2]


def bench_arguments() -> str:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    found = re.search(r"^BENCH_ARGS := (.+)$", makefile, re.MULTILINE)
    assert found is not None, "the Makefile defines BENCH_ARGS"
    return found.group(1)


def test_make_bench_passes_the_committed_levels_and_seeds() -> None:
    plan = load_bench_seeds()
    arguments = bench_arguments().split()

    levels = [int(arguments[i + 1]) for i, word in enumerate(arguments) if word == "--level"]
    assert tuple(levels) == plan.levels
    assert arguments[arguments.index("--seeds") + 1] == str(plan.seed_count)
    assert arguments[arguments.index("--seed-start") + 1] == str(plan.seed_start)
    assert arguments[arguments.index("--workflows") + 1] == "workflows/examples"


def test_the_smoke_cells_are_cells_of_the_published_grid() -> None:
    plan = load_bench_seeds()

    assert plan.smoke.level in plan.levels
    assert set(plan.smoke_seeds) <= set(plan.seeds)
    assert set(plan.smoke.systems) <= set(SYSTEM_IDS)
    assert plan.seeds == tuple(range(1000, 1020))


def test_a_smoke_plan_outside_the_grid_is_refused(tmp_path: Path) -> None:
    text = BENCH_SEEDS_PATH.read_text(encoding="utf-8").replace('"level": 5', '"level": 4')
    path = tmp_path / "bench_seeds.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="cells of the published grid"):
        load_bench_seeds(path)


def test_the_bench_target_passes_an_operator_note_through_when_one_is_given() -> None:
    """Conditions a run was measured under are data, not something the code can guess."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert '$(if $(BENCH_NOTE),--note "$(BENCH_NOTE)",)' in makefile
    assert "BENCH_NOTE ?=" in makefile
