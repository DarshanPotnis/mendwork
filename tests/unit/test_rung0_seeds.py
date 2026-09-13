"""The chaos seed survey: seed ranges, stop attribution, and what each line reports."""

import argparse
from datetime import UTC, datetime

import pytest

from benchmarks.chaos.ground_truth import ActionCheck
from benchmarks.chaos.rung0_seeds import (
    Applied,
    SeedOutcome,
    attribute,
    checked_actions,
    describe,
    false_successes,
    parse_seeds,
    summary,
    wrong_actions,
)
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
            category="heal_expected",
            target_key="login.email",
            description='Renamed the "Email address" field label to "Work email"',
        ),
        Applied(
            id="extra_wrappers",
            category="heal_expected",
            target_key="login.sign_in",
            description="Wrapped sign in",
        ),
    ),
    "dashboard": (
        Applied(
            id="cookie_banner",
            category="heal_expected",
            target_key=None,
            description="Added a banner",
        ),
    ),
    "reports": (),
}


def run(
    status: str,
    failed_step: str | None = None,
    *,
    error_type: str = "TargetDrifted",
    heal: dict[str, object] | None = None,
) -> Run:
    steps = [
        {"step_id": "open_sign_in", "index": 0, "action": "navigate", "status": "succeeded"},
        {
            "step_id": "fill_email",
            "index": 1,
            "action": "fill",
            "status": "succeeded",
            "heal": heal,
        },
    ]
    error = None
    if failed_step is not None:
        steps = [
            steps[0],
            {"step_id": failed_step, "index": 1, "action": "fill", "status": "failed"},
        ]
        error = {
            "type": error_type,
            "message": "stopped",
            "category": "step",
            "context": {"reason": "below_margin"},
        }
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
        "seed 4: stopped at step 2 fill_email with TargetDrifted | checked 0 actions, 0 wrong "
        '| cause: synonym_rename on login.email: Renamed the "Email address" field label to '
        '"Work email" | login: '
    )
    assert "dashboard: cookie_banner(page) | reports: none" in line
    assert describe(SeedOutcome(5, run("succeeded"), APPLIED, ())).startswith(
        "seed 5: succeeded | checked 0 actions, 0 wrong | login: "
    )


def test_a_line_names_the_steps_that_were_healed_and_why_a_heal_abstained() -> None:
    healed = run("succeeded", heal={"healed_rung": 2})
    abstained = run("failed", "fill_email", error_type="HealAbstained")

    assert describe(SeedOutcome(6, healed, APPLIED, ())).startswith(
        "seed 6: succeeded | healed: fill_email at rung 2 | checked 0 actions, 0 wrong | login: "
    )
    assert describe(SeedOutcome(7, abstained, APPLIED, ())).startswith(
        "seed 7: stopped at step 2 fill_email with HealAbstained (below_margin) | checked 0 "
        "actions, 0 wrong | cause: "
    )


def check(
    step_id: str, key: str | None, correct: bool | None, action: str = "click"
) -> ActionCheck:
    return ActionCheck(step_id=step_id, action=action, target_key=key, correct=correct)


def test_only_actions_with_a_ground_truth_target_are_counted() -> None:
    outcome = SeedOutcome(
        1,
        run("succeeded"),
        APPLIED,
        (),
        (check("open_sign_in", None, None), check("fill_email", "login.email", True)),
    )

    assert [item.step_id for item in checked_actions(outcome)] == ["fill_email"]
    assert wrong_actions(outcome) == ()


def test_an_action_on_the_wrong_element_whose_step_succeeded_is_a_false_success() -> None:
    outcome = SeedOutcome(
        2, run("succeeded"), APPLIED, (), (check("fill_email", "login.email", False, "fill"),)
    )

    [wrong] = wrong_actions(outcome)
    assert wrong.step_succeeded
    assert false_successes(outcome) == (wrong,)
    assert "FALSE SUCCESS: fill at fill_email did not reach login.email" in describe(outcome)


def test_any_action_on_an_abstain_target_is_wrong_and_recorded_wrong_actions_count() -> None:
    applied = {
        **APPLIED,
        "reports": (
            Applied(
                id="dangerous_rename",
                category="abstain_expected",
                target_key="login.email",
                description="Relabelled",
            ),
        ),
    }
    stopped = run("failed", "fill_email", error_type="HealAbstained")
    outcome = SeedOutcome(
        3, stopped, applied, (), (check("fill_email", "login.email", True),), ("Delete data",)
    )

    details = [item.detail for item in wrong_actions(outcome)]
    assert details == [
        "click at fill_email on login.email, which must abstain",
        "the page recorded a wrong action: Delete data",
    ]
    assert false_successes(outcome) == ()
    assert "2 wrong: click at fill_email" in describe(outcome)


def test_the_summary_totals_ground_truth_across_seeds() -> None:
    clean = SeedOutcome(
        4, run("succeeded"), APPLIED, (), (check("fill_email", "login.email", True),)
    )
    false = SeedOutcome(
        5, run("succeeded"), APPLIED, (), (check("fill_email", "login.email", False),)
    )

    assert summary([clean, false], "http://portal.test/", 3).splitlines()[0] == (
        "ground truth: 2 actions checked, 1 wrong, 1 false successes across 2 seeds"
    )
