"""The Rung 0 seed survey: seed ranges, stop attribution, and what each line reports."""

import argparse
from datetime import UTC, datetime

import pytest

from benchmarks.chaos.rung0_seeds import Applied, SeedOutcome, attribute, describe, parse_seeds
from mendwork.engine.domain.runs import Run

TARGETS = {
    "fill_email": "login.email",
    "sign_in": "login.sign_in",
    "download_csv": "reports.download_csv",
}
APPLIED = {
    "login": (
        Applied(
            id="synonym_rename",
            target_key="login.email",
            description='Renamed the "Email address" field label to "Work email"',
        ),
        Applied(id="extra_wrappers", target_key="login.sign_in", description="Wrapped sign in"),
    ),
    "dashboard": (Applied(id="cookie_banner", target_key=None, description="Added a banner"),),
    "reports": (),
}


def run(status: str, failed_step: str | None = None) -> Run:
    steps = [
        {"step_id": "open_sign_in", "index": 0, "action": "navigate", "status": "succeeded"},
        {"step_id": "fill_email", "index": 1, "action": "fill", "status": "succeeded"},
    ]
    error = None
    if failed_step is not None:
        steps = [
            steps[0],
            {"step_id": failed_step, "index": 1, "action": "fill", "status": "failed"},
        ]
        error = {"type": "TargetDrifted", "message": "drifted", "category": "step"}
    return Run.model_validate(
        {
            "run_id": "20260911T000000Z-00000001",
            "workflow_id": "download_report",
            "workflow_version": 1,
            "status": status,
            "started_at": datetime(2026, 9, 11, tzinfo=UTC),
            "steps": steps,
            "error": error,
        }
    )


@pytest.mark.parametrize(("text", "seeds"), [("7", range(7, 8)), ("0-3", range(0, 4))])
def test_seeds_are_one_number_or_an_inclusive_range(text: str, seeds: range) -> None:
    assert parse_seeds(text) == seeds


@pytest.mark.parametrize("text", ["", "a-3", "5-2", "-1"])
def test_malformed_seed_ranges_are_refused(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_seeds(text)


def test_a_stop_is_attributed_to_the_mutations_on_the_failing_steps_target() -> None:
    assert attribute(run("failed", "fill_email"), TARGETS, APPLIED) == (APPLIED["login"][0],)


def test_success_and_untargeted_steps_have_no_cause() -> None:
    assert attribute(run("succeeded"), TARGETS, APPLIED) == ()
    assert attribute(run("failed", "open_sign_in"), TARGETS, APPLIED) == ()


def test_each_line_says_where_the_run_stopped_and_why() -> None:
    stopped = run("failed", "fill_email")
    line = describe(SeedOutcome(4, stopped, APPLIED, attribute(stopped, TARGETS, APPLIED)))

    assert line.startswith(
        "seed 4: stopped at step 2 fill_email with TargetDrifted | cause: synonym_rename on "
        'login.email: Renamed the "Email address" field label to "Work email" | login: '
    )
    assert "dashboard: cookie_banner(page) | reports: none" in line
    assert describe(SeedOutcome(5, run("succeeded"), APPLIED, ())).startswith(
        "seed 5: succeeded | login: "
    )
