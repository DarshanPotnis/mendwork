"""Where the heal fixture suite leaves its outcomes, and how the terminal summary shows them."""

from collections.abc import Mapping
from typing import Final

import pytest

from benchmarks.chaos.heal_cases import CaseOutcome, render_table, unresolved_heals

HEAL_SUITE_OUTCOMES: Final = pytest.StashKey[Mapping[str, CaseOutcome]]()


def summary_lines(outcomes: Mapping[str, CaseOutcome], *, verbose: bool) -> list[str]:
    """The per-mutation table, the heal cases Rung 2 did not resolve, and with -v every case."""
    lines = render_table(outcomes).splitlines()
    gaps = unresolved_heals(outcomes)
    wrong = sum(len(outcome.wrong) for outcome in outcomes.values())
    lines.append(f"wrong actions across the suite: {wrong}")
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
