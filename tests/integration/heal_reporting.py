"""Where the heal fixture suite leaves its outcomes, and how the terminal summary shows them.

Serially, the suite's module fixture and the terminal summary share one pytest process. Under
pytest-xdist (ADR 0012) the suite runs in a worker while the controller prints the summary, so a
worker also sends the outcomes as JSON through ``config.workeroutput``, and the controller rebuilds
them when the worker finishes (``tests/conftest.py``).
"""

from collections.abc import Mapping
from typing import Final

import pytest
from pydantic import TypeAdapter

from benchmarks.chaos.heal_cases import CaseOutcome, render_table, unresolved_heals

HEAL_SUITE_OUTCOMES: Final = pytest.StashKey[Mapping[str, CaseOutcome]]()
WORKER_OUTPUT_KEY: Final = "heal_suite_outcomes"
_OUTCOMES: Final = TypeAdapter(dict[str, CaseOutcome])


def encode_outcomes(outcomes: Mapping[str, CaseOutcome]) -> str:
    """The outcomes as JSON, which is what a worker can send its controller."""
    return _OUTCOMES.dump_json(dict(outcomes)).decode("utf-8")


def decode_outcomes(data: str) -> dict[str, CaseOutcome]:
    """Outcomes a worker sent, rebuilt exactly."""
    return _OUTCOMES.validate_json(data)


def record_outcomes(config: pytest.Config, outcomes: Mapping[str, CaseOutcome]) -> None:
    """Keep the outcomes for this process's summary, and send them on when this is a worker."""
    config.stash[HEAL_SUITE_OUTCOMES] = outcomes
    # pytest-xdist sets ``workeroutput`` on a worker's config only; serially it is absent.
    workeroutput = getattr(config, "workeroutput", None)
    if isinstance(workeroutput, dict):
        workeroutput[WORKER_OUTPUT_KEY] = encode_outcomes(outcomes)


def summary_lines(outcomes: Mapping[str, CaseOutcome], *, verbose: bool) -> list[str]:
    """The per-mutation table, the heal cases Rung 2 did not resolve, and with -v every case."""
    lines = render_table(outcomes).splitlines()
    gaps = unresolved_heals(outcomes)
    wrong = sum(len(outcome.wrong) for outcome in outcomes.values())
    lines.append(f"wrong actions across the suite: {wrong}")
    healed = [outcome for outcome in outcomes.values() if outcome.captured is not None]
    captured = sum(1 for outcome in healed if outcome.captured)
    lines.append(f"heals that captured a fingerprint: {captured} of {len(healed)}")
    lines.append(
        "heal_expected cases not resolved: "
        + (", ".join(outcome.case.id for outcome in gaps) or "none")
    )
    if verbose:
        lines.extend(
            outcome.summary()
            for outcome in sorted(outcomes.values(), key=lambda item: item.case.id)
        )
    return lines
