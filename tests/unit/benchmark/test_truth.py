"""Ground truth for steps, from the changes a page received (ADR 0014)."""

import pytest

from mendwork.engine.benchmark.truth import (
    AppliedMutation,
    Expectation,
    MutationCategory,
    Resolution,
    TargetMatch,
    resolution_for_rung,
    step_truths,
    target_match,
)

TARGETS = {"sign_in": "login.sign_in", "open_reports": "dashboard.open_reports"}


def test_a_step_whose_control_got_an_abstain_change_expects_abstention() -> None:
    truths = step_truths(
        TARGETS,
        [
            AppliedMutation(
                mutation="dangerous_rename",
                category=MutationCategory.ABSTAIN_EXPECTED,
                target_key="dashboard.open_reports",
            )
        ],
    )

    assert truths["open_reports"].expectation is Expectation.ABSTAIN
    assert truths["sign_in"].expectation is Expectation.ACT
    assert not truths["sign_in"].target_changed


def test_heal_changes_are_listed_sorted_and_page_changes_are_not_a_controls() -> None:
    truths = step_truths(
        TARGETS,
        [
            AppliedMutation(
                mutation="synonym_rename",
                category=MutationCategory.HEAL_EXPECTED,
                target_key="login.sign_in",
            ),
            AppliedMutation(
                mutation="change_ids_classes",
                category=MutationCategory.HEAL_EXPECTED,
                target_key="login.sign_in",
            ),
            AppliedMutation(mutation="cookie_banner", category=MutationCategory.HEAL_EXPECTED),
            AppliedMutation(
                mutation="synonym_rename",
                category=MutationCategory.HEAL_EXPECTED,
                target_key="orders.view_order",
            ),
        ],
    )

    assert truths["sign_in"].changes == ("change_ids_classes", "synonym_rename")
    assert truths["sign_in"].expectation is Expectation.ACT
    assert truths["sign_in"].target_changed
    assert truths["open_reports"].changes == ()
    assert set(truths) == set(TARGETS)


@pytest.mark.parametrize(
    ("rung", "resolution", "healed"),
    [
        (0, Resolution.DIRECT, False),
        (1, Resolution.RUNG_1, True),
        (2, Resolution.RUNG_2, True),
        (3, Resolution.RUNG_3, True),
    ],
)
def test_each_rung_has_its_resolution(rung: int, resolution: Resolution, healed: bool) -> None:
    assert resolution_for_rung(rung) is resolution
    assert resolution.healed is healed


def test_there_is_no_fourth_rung_to_resolve_from() -> None:
    with pytest.raises(ValueError, match="no rung 4"):
        resolution_for_rung(4)


def test_only_a_ground_truth_that_answered_can_call_an_action_off_target() -> None:
    assert target_match(matched=True, known=True) is TargetMatch.ON_TARGET
    assert target_match(matched=False, known=True) is TargetMatch.OFF_TARGET
    assert target_match(matched=False, known=False) is TargetMatch.UNKNOWN
    # A truth that named the element cannot also be a truth that said nothing.
    assert target_match(matched=True, known=False) is TargetMatch.ON_TARGET
