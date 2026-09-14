"""Rung 3 on complete download_report runs, answered from ground truth.

Of the six Rung 2 stops Phase 5 found (level 3 seeds 3 and 15; level 5 seeds 3, 9, 10, and 11),
five must heal at Rung 3 with the model's choice on the real target. At level 3 seed 15 the
download button moved away from every text recorded near it, so the context veto refuses even the
right choice: the cost ADR 0010 states. A confidently wrong model on level 5 seed 32, where a
navigation link reaches the same page the removed target did, must be refused before it acts. No
action in any run may reach a wrong element. A run that needs no heal, and one Rung 2 heals, make
no model call.
"""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import pytest
import pytest_asyncio
from playwright.async_api import Browser

from benchmarks.chaos.rung3_eval import KNOWN_CASES, CaseResult, SeedCase, run_seed
from benchmarks.chaos.workflow_targets import load_workflow_targets
from mendwork.settings import Settings
from tests.workflows import load_example

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

KNOWN: Final = tuple(SeedCase(level, seed) for level, seed in KNOWN_CASES)
HEALED_AT_RUNG3: Final = {
    SeedCase(3, 3): "open_reports",
    SeedCase(5, 3): "fill_email",
    SeedCase(5, 9): "fill_email",
    SeedCase(5, 10): "fill_email",
    SeedCase(5, 11): "fill_password",
}
CONTEXT_MOVED: Final = SeedCase(3, 15)
SAME_DESTINATION: Final = SeedCase(5, 32)
UNHEALED_LEVEL_0: Final = SeedCase(0, 0)
RUNG2_HEALED: Final = SeedCase(3, 0)
CONCURRENCY: Final = 3


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def results(
    browser: Browser, portal_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Mapping[SeedCase, CaseResult]:
    settings = Settings(_env_file=None).model_copy(update={"trace_on_failure": False})
    workflow = load_example("download_report")
    targets = load_workflow_targets("download_report").targets
    directory: Path = tmp_path_factory.mktemp("rung3-known")
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(case: SeedCase) -> CaseResult:
        async with limit:
            return await run_seed(
                browser,
                portal_url,
                workflow,
                targets,
                case,
                directory / f"{case.level}-{case.seed}",
                settings=settings,
                model="adversarial" if case == SAME_DESTINATION else "oracle",
                client=None,
            )

    cases = (*KNOWN, UNHEALED_LEVEL_0, RUNG2_HEALED, SAME_DESTINATION)
    return dict(zip(cases, await asyncio.gather(*(one(case) for case in cases)), strict=True))


@pytest.mark.parametrize("case", tuple(HEALED_AT_RUNG3), ids=lambda case: case.label)
async def test_each_known_rung2_stop_heals_at_rung3_on_the_real_target(
    results: Mapping[SeedCase, CaseResult], case: SeedCase
) -> None:
    result = results[case]
    step = HEALED_AT_RUNG3[case]

    [decision] = [d for d in result.decisions if d.step_id == step]
    assert (decision.rung2_outcome, decision.outcome) in {
        ("below_threshold", "resolved"),
        ("below_margin", "resolved"),
    }
    assert (decision.target_listed, decision.chose_target, decision.verification) == (
        True,
        True,
        "passed",
    )
    assert f"{step}@r3" in result.healed


async def test_a_right_choice_far_from_its_recorded_context_is_refused(
    results: Mapping[SeedCase, CaseResult],
) -> None:
    result = results[CONTEXT_MOVED]

    [decision] = [d for d in result.decisions if d.step_id == "download_csv"]
    assert (decision.chose_target, decision.outcome, decision.refused) == (
        True,
        "choice_refused",
        "context_lost",
    )
    assert (result.stopped_at, result.stop_reason) == ("download_csv", "model_choice_refused")


async def test_a_confident_pick_of_a_same_destination_link_is_refused_before_it_acts(
    results: Mapping[SeedCase, CaseResult],
) -> None:
    result = results[SAME_DESTINATION]

    decisions = [d for d in result.decisions if d.step_id == "open_reports"]
    assert decisions
    assert {(d.chose_target, d.outcome, d.verification) for d in decisions} == {
        (False, "choice_refused", "not_performed")
    }
    assert result.stopped_at == "open_reports"


async def test_no_run_acted_on_a_wrong_element_or_succeeded_on_one(
    results: Mapping[SeedCase, CaseResult],
) -> None:
    assert {
        case.label: result.wrong_actions for case, result in results.items() if result.wrong_actions
    } == {}
    assert {
        case.label: result.false_successes
        for case, result in results.items()
        if result.false_successes
    } == {}


async def test_every_model_call_is_counted_in_the_run_record(
    results: Mapping[SeedCase, CaseResult],
) -> None:
    for result in results.values():
        assert result.model_usage.calls == sum(decision.calls for decision in result.decisions)
        assert result.model_usage.estimated_cost_usd == 0


async def test_runs_that_need_no_model_make_no_model_call(
    results: Mapping[SeedCase, CaseResult],
) -> None:
    unhealed = results[UNHEALED_LEVEL_0]
    rung2 = results[RUNG2_HEALED]

    assert (unhealed.status, unhealed.healed, unhealed.model_usage.calls) == ("succeeded", (), 0)
    assert rung2.status == "succeeded"
    assert "download_csv@r2" in rung2.healed
    assert rung2.model_usage.calls == 0
    assert rung2.decisions == ()
