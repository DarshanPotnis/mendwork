"""The heal fixture suite: every mutation the example workflows can meet, against ground truth.

Cases and their runner live in benchmarks/chaos/heal_cases.py, shared with
``python -m benchmarks.chaos.heal_suite``. All cases run once per session, each in its own
browser context, a few at a time; every test then reads its own case's outcome, so tests do
not depend on each other's order. A wrong action fails its case and the suite-wide check.
"""

from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser

from benchmarks.chaos.heal_cases import (
    CaseOutcome,
    HealCase,
    build_cases,
    load_workflows,
    run_cases,
)
from benchmarks.chaos.heal_pairs import ABSTAIN, ABSTAIN_TABLE_PATH, HEAL, load_table
from tests.integration.heal_reporting import record_outcomes

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

WORKFLOWS = load_workflows()
HEAL_TABLE = load_table()
ABSTAIN_TABLE = load_table(ABSTAIN_TABLE_PATH)
CASES = build_cases(WORKFLOWS, HEAL_TABLE, ABSTAIN_TABLE)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def heal_suite(
    browser: Browser,
    portal_url: str,
    tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
) -> Mapping[str, CaseOutcome]:
    directory: Path = tmp_path_factory.mktemp("heal-suite")
    outcomes = await run_cases(browser, portal_url, CASES, WORKFLOWS, directory, model="oracle")
    record_outcomes(request.config, outcomes)
    return outcomes


async def test_the_suite_covers_every_pair_the_example_workflows_can_reach() -> None:
    targets = {key for workflow in WORKFLOWS.values() for key in workflow.targets.values()}
    expected = {
        (category, pair.mutation, pair.target)
        for category, table in ((HEAL, HEAL_TABLE), (ABSTAIN, ABSTAIN_TABLE))
        for pair in table.pairs
        if pair.target in targets
    }
    pages = {key.split(".", 1)[0] for key in targets}

    covered = {(case.category, case.mutation, case.target) for case in CASES if case.target}
    page_cases = {case.page for case in CASES if case.target is None}

    assert covered == expected
    assert page_cases == pages
    assert Counter(case.category for case in CASES) == {HEAL: 52, ABSTAIN: 15}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
async def test_each_case_is_healed_or_abstains_as_expected(
    heal_suite: Mapping[str, CaseOutcome], case: HealCase
) -> None:
    outcome = heal_suite[case.id]

    assert outcome.wrong == (), outcome.wrong
    assert outcome.verdict is case.expected, outcome.detail


async def test_no_case_in_the_suite_acted_on_a_wrong_element(
    heal_suite: Mapping[str, CaseOutcome],
) -> None:
    wrong = {case_id: outcome.wrong for case_id, outcome in heal_suite.items() if outcome.wrong}

    assert wrong == {}


async def test_every_heal_fingerprinted_the_element_it_acted_on_so_it_could_become_a_version(
    heal_suite: Mapping[str, CaseOutcome],
) -> None:
    healed = {case_id for case_id, outcome in heal_suite.items() if outcome.captured is not None}
    uncaptured = {case_id for case_id, outcome in heal_suite.items() if outcome.captured is False}

    assert uncaptured == set()
    assert len(healed) == 14


async def test_rung3_only_takes_up_what_rung2_declined_and_asks_only_when_it_can_help(
    heal_suite: Mapping[str, CaseOutcome],
) -> None:
    heal_rungs = Counter(
        outcome.rung for outcome in heal_suite.values() if outcome.case.category == HEAL
    )
    calls = {
        case_id: outcome.model_calls
        for case_id, outcome in heal_suite.items()
        if outcome.model_calls
    }

    assert heal_rungs == {0: 38, 2: 14}
    assert calls == {"remove_target-dashboard.open_reports": 1}
