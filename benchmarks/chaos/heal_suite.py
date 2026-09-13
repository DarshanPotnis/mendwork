"""Run the heal fixture suite outside pytest and print its per-mutation table.

Serves the chaos portal, launches Chromium, and runs every case (see heal_cases). With
``--repeat N`` the suite runs N times and every case's outcome must be identical in each run.

    MENDWORK_LOG_LEVEL=WARNING uv run python -m benchmarks.chaos.heal_suite --repeat 3 --cases
"""

import argparse
import asyncio
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from playwright.async_api import async_playwright

from benchmarks.chaos.heal_cases import (
    SUITE_CONCURRENCY,
    CaseOutcome,
    build_cases,
    load_workflows,
    render_table,
    run_cases,
    unresolved_heals,
)
from benchmarks.chaos.heal_pairs import ABSTAIN_TABLE_PATH, PORTAL_ROOT, load_table
from mendwork.apps.portal.server import PortalServer
from mendwork.observability import configure_logging
from mendwork.settings import Settings


async def suite(repeat: int, concurrency: int, show_cases: bool, out: TextIO) -> int:
    """Run the suite ``repeat`` times; 0 when every case is as expected and identical each time."""
    configure_logging(Settings())
    workflows = load_workflows()
    cases = build_cases(workflows, load_table(), load_table(ABSTAIN_TABLE_PATH))
    runs: list[dict[str, CaseOutcome]] = []
    with PortalServer(PORTAL_ROOT, host="127.0.0.1", port=0) as server:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                for number in range(1, repeat + 1):
                    with tempfile.TemporaryDirectory(prefix="mendwork-heal-suite-") as scratch:
                        outcomes = await run_cases(
                            browser, server.url, cases, workflows, Path(scratch),
                            concurrency=concurrency,
                        )  # fmt: skip
                    runs.append(outcomes)
                    _report(number, outcomes, show_cases, out)
            finally:
                await browser.close()
    unexpected = [
        outcome
        for outcomes in runs
        for outcome in outcomes.values()
        if outcome.wrong or outcome.verdict is not outcome.case.expected
    ]
    varying = _varying(runs)
    if len(runs) > 1:
        out.write(
            f"\nrepeatability: {len(varying)} of {len(cases)} cases varied across "
            f"{len(runs)} runs\n"
        )
        for case_id in varying:
            out.write(f"  {case_id}: " + " | ".join(run[case_id].summary() for run in runs) + "\n")
    return 1 if unexpected or varying else 0


def _report(number: int, outcomes: dict[str, CaseOutcome], show_cases: bool, out: TextIO) -> None:
    out.write(f"\nrun {number}\n{render_table(outcomes)}\n")
    gaps = unresolved_heals(outcomes)
    out.write(
        "heal_expected cases not resolved: "
        + (", ".join(outcome.case.id for outcome in gaps) or "none")
        + "\n"
    )
    if show_cases:
        for outcome in sorted(outcomes.values(), key=lambda item: item.case.id):
            out.write(f"  {outcome.summary()}\n")
            for problem in outcome.wrong:
                out.write(f"    WRONG: {problem}\n")
            if outcome.verdict is not outcome.case.expected:
                out.write(f"    expected {outcome.case.expected}: {outcome.detail}\n")


def _varying(runs: Sequence[dict[str, CaseOutcome]]) -> list[str]:
    if not runs:
        return []
    return sorted(
        case_id for case_id in runs[0] if len({run[case_id].summary() for run in runs}) > 1
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.chaos.heal_suite", description=__doc__
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=SUITE_CONCURRENCY)
    parser.add_argument("--cases", action="store_true", help="print every case's outcome")
    arguments = parser.parse_args(argv)
    return asyncio.run(suite(arguments.repeat, arguments.concurrency, arguments.cases, sys.stdout))


if __name__ == "__main__":
    raise SystemExit(main())
