"""The heal fixture suite's outcomes survive the trip from an xdist worker to the controller."""

from dataclasses import dataclass, field

import pytest
from hypothesis import given
from hypothesis import strategies as st

from benchmarks.chaos.heal_cases import CaseOutcome, HealCase, Verdict
from benchmarks.chaos.heal_pairs import ABSTAIN, HEAL
from tests.conftest import pytest_testnodedown
from tests.integration.heal_reporting import (
    HEAL_SUITE_OUTCOMES,
    WORKER_OUTPUT_KEY,
    decode_outcomes,
    encode_outcomes,
    record_outcomes,
    summary_lines,
)

TEXT = st.text(min_size=1, max_size=12)
CASES = st.builds(
    HealCase,
    page=TEXT,
    mutation=TEXT,
    target=st.none() | TEXT,
    seed=st.integers(min_value=0, max_value=1_000_000),
    category=st.sampled_from([HEAL, ABSTAIN]),
    workflow_id=TEXT,
    step_ids=st.lists(TEXT, max_size=4).map(tuple),
    target_step=TEXT,
)
OUTCOMES = st.builds(
    CaseOutcome,
    case=CASES,
    verdict=st.sampled_from(list(Verdict)),
    rung=st.none() | st.integers(min_value=0, max_value=3),
    reason=st.none() | TEXT,
    wrong=st.lists(TEXT, max_size=3).map(tuple),
    detail=st.text(max_size=40),
    model_calls=st.integers(min_value=0, max_value=5),
)


def bare_config() -> pytest.Config:
    """A config with only a stash, which is all the reporting reads."""
    config = pytest.Config.__new__(pytest.Config)
    config.stash = pytest.Stash()
    return config


@dataclass
class Finished:
    """A finished worker as the controller sees it."""

    config: pytest.Config
    workeroutput: dict[str, object] = field(default_factory=dict)


def wrong_case(case_id: str = "swap") -> CaseOutcome:
    case = HealCase(
        "reports", case_id, "reports.download_csv", 3, HEAL, "download_report", ("a",), "a"
    )
    return CaseOutcome(
        case, Verdict.WRONG, 2, None, ("click at a reached reports.apply_filter",), ""
    )


@given(st.lists(OUTCOMES, max_size=5))
def test_outcomes_round_trip_exactly_through_the_worker_encoding(
    outcomes: list[CaseOutcome],
) -> None:
    keyed = {f"{position}-{outcome.case.id}": outcome for position, outcome in enumerate(outcomes)}

    assert decode_outcomes(encode_outcomes(keyed)) == keyed


def test_a_worker_sends_its_outcomes_and_a_serial_run_only_keeps_them() -> None:
    outcomes = {"swap": wrong_case()}
    worker, serial = bare_config(), bare_config()
    sent: dict[str, object] = {}
    worker.workeroutput = sent  # type: ignore[attr-defined]  # what pytest-xdist sets on a worker

    record_outcomes(worker, outcomes)
    record_outcomes(serial, outcomes)

    encoded = sent[WORKER_OUTPUT_KEY]
    assert isinstance(encoded, str)
    assert decode_outcomes(encoded) == outcomes
    assert worker.stash[HEAL_SUITE_OUTCOMES] == serial.stash[HEAL_SUITE_OUTCOMES] == outcomes
    assert not hasattr(serial, "workeroutput")


def test_the_controller_rebuilds_a_workers_outcomes_and_reports_its_wrong_actions() -> None:
    controller = bare_config()
    node = Finished(controller, {WORKER_OUTPUT_KEY: encode_outcomes({"swap": wrong_case()})})

    pytest_testnodedown(node, None)

    rebuilt = controller.stash[HEAL_SUITE_OUTCOMES]
    assert rebuilt == {"swap": wrong_case()}
    assert "wrong actions across the suite: 1" in summary_lines(rebuilt, verbose=False)


def test_a_worker_that_did_not_run_the_suite_changes_nothing() -> None:
    controller = bare_config()

    pytest_testnodedown(Finished(controller), None)

    assert HEAL_SUITE_OUTCOMES not in controller.stash
