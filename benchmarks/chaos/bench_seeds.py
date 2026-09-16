"""The published benchmark's seeds and levels, and the CI smoke subset of them (ADR 0014).

Fixed in ``bench_seeds.json`` before any benchmark ran. ``make bench`` passes the same numbers on
its command line, and a unit test keeps the two from drifting apart.
"""

from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

BENCH_SEEDS_PATH: Final = Path(__file__).resolve().with_name("bench_seeds.json")


class SmokePlan(BaseModel):
    """The cells CI runs on every change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: int = Field(ge=0, le=5)
    seed_count: int = Field(ge=1)
    step_timeout_ms: int = Field(ge=1)
    systems: tuple[str, ...]


class BenchSeeds(BaseModel):
    """The published grid's levels and seeds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    purpose: str
    chosen_on: str
    rule: str
    levels: tuple[int, ...]
    seed_start: int = Field(ge=0)
    seed_count: int = Field(ge=1)
    smoke: SmokePlan

    @property
    def seeds(self) -> tuple[int, ...]:
        """Every published seed."""
        return tuple(range(self.seed_start, self.seed_start + self.seed_count))

    @property
    def smoke_seeds(self) -> tuple[int, ...]:
        """The first seeds of the published grid, which the smoke benchmark reruns."""
        return self.seeds[: self.smoke.seed_count]


def load_bench_seeds(path: Path = BENCH_SEEDS_PATH) -> BenchSeeds:
    """The committed seed plan, validated; the smoke subset must lie inside the published grid."""
    plan = BenchSeeds.model_validate_json(path.read_text(encoding="utf-8"))
    if plan.smoke.level not in plan.levels or plan.smoke.seed_count > plan.seed_count:
        raise ValueError(f"{path}: the smoke cells must be cells of the published grid")
    return plan
