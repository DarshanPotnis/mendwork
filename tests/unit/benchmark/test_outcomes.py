"""Every outcome class, the order its rules apply in, and how a wrong action was noticed."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from mendwork.engine.benchmark.outcomes import (
    OutcomeClass,
    StepOutcome,
    WrongKind,
    classify_step,
)
from mendwork.engine.benchmark.truth import (
    ActionRecord,
    Expectation,
    Resolution,
    StepObservation,
    StopKind,
    TargetMatch,
    Verdict,
)
from mendwork.engine.domain.enums import ActionType, VerificationStrength
from tests.unit.benchmark.builders import action, observation, truth

OFF = TargetMatch.OFF_TARGET
UNKNOWN = TargetMatch.UNKNOWN


def test_a_step_the_run_never_started_is_not_reached() -> None:
    outcome = classify_step(truth(), observation(stop=StopKind.NOT_REACHED))

    assert outcome.outcome is OutcomeClass.NOT_REACHED
    assert not outcome.reached


def test_the_recorded_selectors_acting_on_the_real_control_is_a_direct_correct_step() -> None:
    outcome = classify_step(truth(), observation(actions=[action()]))

    assert (outcome.outcome, outcome.resolution, outcome.wrong) == (
        OutcomeClass.DIRECT_CORRECT,
        Resolution.DIRECT,
        None,
    )


@pytest.mark.parametrize("rung", [Resolution.RUNG_1, Resolution.RUNG_2, Resolution.RUNG_3])
def test_a_heal_that_acted_on_the_real_control_and_passed_is_healed_correct(
    rung: Resolution,
) -> None:
    outcome = classify_step(
        truth(changes=["synonym_rename"]),
        observation(actions=[action(resolution=rung)], ladder_ran=True),
    )

    assert (outcome.outcome, outcome.resolution) == (OutcomeClass.HEALED_CORRECT, rung)


def test_a_wrong_heal_that_was_caught_and_undone_is_still_a_wrong_step() -> None:
    outcome = classify_step(
        truth(changes=["synonym_rename"]),
        observation(
            actions=[
                action(resolution=Resolution.RUNG_3, on_target=OFF, verdict=Verdict.FAILED),
                action(during_restore=True, verdict=Verdict.NOT_CHECKED),
                action(resolution=Resolution.RUNG_2),
            ],
            ladder_ran=True,
        ),
    )

    assert (outcome.outcome, outcome.wrong, outcome.resolution) == (
        OutcomeClass.HEALED_WRONG,
        WrongKind.CAUGHT,
        Resolution.RUNG_3,
    )
    assert (outcome.actions, outcome.wrong_actions) == (3, 1)


def test_a_wrong_action_whose_checkpoints_passed_is_a_false_success() -> None:
    outcome = classify_step(
        truth(),
        observation(
            actions=[action(resolution=Resolution.RUNG_3, on_target=OFF)],
            strength=VerificationStrength.WEAK,
        ),
    )

    assert (outcome.outcome, outcome.wrong) == (OutcomeClass.HEALED_WRONG, WrongKind.FALSE_SUCCESS)


def test_a_scripts_locator_reaching_the_wrong_element_is_a_direct_wrong_step() -> None:
    outcome = classify_step(
        truth(),
        observation(
            stop=StopKind.CHECKPOINT_FAILED,
            actions=[action(on_target=OFF, verdict=Verdict.FAILED)],
        ),
    )

    assert (outcome.outcome, outcome.wrong) == (OutcomeClass.DIRECT_WRONG, WrongKind.CAUGHT)


def test_activating_the_control_itself_at_an_abstain_step_is_wrong() -> None:
    # dangerous_rename keeps the id: the element is the step's control, and acting on it is wrong.
    outcome = classify_step(
        truth(expectation=Expectation.ABSTAIN, changes=()),
        observation(
            stop=StopKind.CHECKPOINT_FAILED,
            actions=[action(on_target=TargetMatch.ON_TARGET, verdict=Verdict.FAILED)],
        ),
    )

    assert (outcome.outcome, outcome.wrong, outcome.wrong_actions) == (
        OutcomeClass.DIRECT_WRONG,
        WrongKind.CAUGHT,
        1,
    )


@pytest.mark.parametrize(
    ("stop", "kind"),
    [(StopKind.COMPLETED, WrongKind.FALSE_SUCCESS), (StopKind.CHECKPOINT_FAILED, WrongKind.CAUGHT)],
)
def test_a_wrong_action_only_the_page_recorded_is_wrong_without_a_resolution(
    stop: StopKind, kind: WrongKind
) -> None:
    outcome = classify_step(truth(), observation(stop=stop, page_wrong=1))

    assert (outcome.outcome, outcome.wrong, outcome.resolution, outcome.wrong_actions) == (
        OutcomeClass.DIRECT_WRONG,
        kind,
        None,
        1,
    )


def test_actions_replaying_earlier_steps_during_a_restore_are_not_wrong() -> None:
    outcome = classify_step(
        truth(),
        observation(
            actions=[
                action(resolution=Resolution.RUNG_2, verdict=Verdict.FAILED),
                action(during_restore=True, verdict=Verdict.NOT_CHECKED, kind=ActionType.FILL),
                action(resolution=Resolution.RUNG_2),
            ],
            ladder_ran=True,
        ),
    )

    assert outcome.outcome is OutcomeClass.HEALED_CORRECT


def test_declining_at_an_abstain_step_without_acting_is_a_correct_abstention() -> None:
    outcome = classify_step(
        truth(expectation=Expectation.ABSTAIN),
        observation(stop=StopKind.DECLINED, reason="top_rejected", ladder_ran=True),
    )

    assert (outcome.outcome, outcome.stop_reason) == (
        OutcomeClass.ABSTAINED_CORRECT,
        "top_rejected",
    )


def test_declining_while_the_real_control_was_there_is_an_unnecessary_abstention() -> None:
    outcome = classify_step(
        truth(changes=["synonym_rename"]),
        observation(stop=StopKind.DECLINED, reason="weak_verification", available=True),
    )

    assert outcome.outcome is OutcomeClass.ABSTAINED_UNNECESSARY


@pytest.mark.parametrize("available", [False, None])
def test_declining_when_the_real_control_could_not_be_confirmed_is_a_failure(
    available: bool | None,
) -> None:
    outcome = classify_step(
        truth(), observation(stop=StopKind.DECLINED, reason="not_found", available=available)
    )

    assert outcome.outcome is OutcomeClass.FAILED


def test_declining_after_acting_correctly_is_a_failure_not_an_abstention() -> None:
    outcome = classify_step(
        truth(),
        observation(
            stop=StopKind.DECLINED,
            reason="restore_failed",
            actions=[action(resolution=Resolution.RUNG_2, verdict=Verdict.FAILED)],
            available=True,
        ),
    )

    assert outcome.outcome is OutcomeClass.FAILED


@pytest.mark.parametrize(
    "stop", [StopKind.ERROR, StopKind.UNEXPRESSIBLE, StopKind.CHECKPOINT_FAILED]
)
def test_stops_that_are_not_decisions_are_failures_even_at_an_abstain_step(stop: StopKind) -> None:
    outcome = classify_step(truth(expectation=Expectation.ABSTAIN), observation(stop=stop))

    assert outcome.outcome is OutcomeClass.FAILED


def test_a_correct_action_whose_checkpoints_failed_is_a_failure() -> None:
    outcome = classify_step(
        truth(),
        observation(stop=StopKind.CHECKPOINT_FAILED, actions=[action(verdict=Verdict.FAILED)]),
    )

    assert outcome.outcome is OutcomeClass.FAILED


def test_completing_an_abstain_step_without_any_action_is_a_failure() -> None:
    outcome = classify_step(truth(expectation=Expectation.ABSTAIN), observation())

    assert outcome.outcome is OutcomeClass.FAILED


@pytest.mark.parametrize("on_target", [True, False, None])
def test_a_stop_for_approval_is_its_own_class_and_keeps_whether_the_proposal_was_right(
    on_target: bool | None,
) -> None:
    outcome = classify_step(
        truth(), observation(stop=StopKind.APPROVAL, proposal_on_target=on_target)
    )

    assert (outcome.outcome, outcome.proposal_on_target) == (
        OutcomeClass.APPROVAL_REQUESTED,
        on_target,
    )


def test_truth_and_observation_must_be_about_the_same_step() -> None:
    with pytest.raises(ValueError, match="cannot classify"):
        classify_step(truth("sign_in"), observation("open_reports"))


def test_an_outcome_cannot_be_wrong_without_saying_how_it_was_noticed() -> None:
    fields = classify_step(truth(), observation(actions=[action()])).model_dump()

    with pytest.raises(ValidationError, match="says how it was noticed"):
        StepOutcome.model_validate({**fields, "outcome": OutcomeClass.DIRECT_WRONG})
    with pytest.raises(ValidationError, match="says how it was noticed"):
        StepOutcome.model_validate({**fields, "wrong": WrongKind.CAUGHT})
    with pytest.raises(ValidationError, match="classified wrong"):
        StepOutcome.model_validate({**fields, "wrong_actions": 1})


actions_strategy = st.builds(
    ActionRecord,
    action=st.sampled_from(ActionType),
    resolution=st.sampled_from(Resolution),
    on_target=st.sampled_from(TargetMatch),
    verdict=st.sampled_from(Verdict),
    during_restore=st.booleans(),
)
observations = st.builds(
    StepObservation,
    step_id=st.just("open_reports"),
    stop=st.sampled_from(StopKind),
    stop_reason=st.none() | st.sampled_from(["below_threshold", "not_found"]),
    actions=st.lists(actions_strategy, max_size=4).map(tuple),
    page_wrong_actions=st.integers(min_value=0, max_value=2),
    target_available_at_stop=st.none() | st.booleans(),
    strength=st.sampled_from(VerificationStrength),
    ladder_ran=st.booleans(),
)


@given(observations, st.sampled_from(Expectation))
def test_any_off_target_action_makes_the_step_wrong(
    observed: StepObservation, expectation: Expectation
) -> None:
    outcome = classify_step(truth(expectation=expectation), observed)
    off_target = any(record.on_target is OFF for record in observed.actions)
    abstain_action = expectation is Expectation.ABSTAIN and any(
        not record.during_restore for record in observed.actions
    )

    if off_target or abstain_action or observed.page_wrong_actions:
        assert outcome.outcome.wrong
        assert outcome.wrong_actions > 0
    else:
        assert not outcome.outcome.wrong
        assert outcome.wrong_actions == 0


@given(observations, st.sampled_from(Expectation))
def test_only_a_step_that_acted_on_nothing_can_be_an_abstention(
    observed: StepObservation, expectation: Expectation
) -> None:
    outcome = classify_step(truth(expectation=expectation), observed)

    if outcome.outcome in (OutcomeClass.ABSTAINED_CORRECT, OutcomeClass.ABSTAINED_UNNECESSARY):
        assert observed.stop is StopKind.DECLINED
        assert not [record for record in observed.actions if not record.during_restore]
        assert (outcome.outcome is OutcomeClass.ABSTAINED_CORRECT) is (
            expectation is Expectation.ABSTAIN
        )


def test_an_action_ground_truth_could_not_judge_is_neither_wrong_nor_correct() -> None:
    outcome = classify_step(truth(), observation(actions=[action(on_target=UNKNOWN)]))

    assert outcome.outcome is OutcomeClass.GROUND_TRUTH_UNKNOWN
    assert not outcome.outcome.wrong
    assert not outcome.outcome.correct_resolution
    assert (outcome.wrong_actions, outcome.unknown_actions) == (0, 1)
    assert outcome.wrong is None


def test_a_wrong_action_beats_an_unjudged_one_at_the_same_step() -> None:
    outcome = classify_step(
        truth(),
        observation(actions=[action(on_target=UNKNOWN), action(on_target=OFF)]),
    )

    assert outcome.outcome is OutcomeClass.DIRECT_WRONG
    assert (outcome.wrong_actions, outcome.unknown_actions) == (1, 1)


def test_acting_at_all_on_an_abstain_step_is_wrong_even_when_the_element_is_unjudged() -> None:
    outcome = classify_step(
        truth(expectation=Expectation.ABSTAIN), observation(actions=[action(on_target=UNKNOWN)])
    )

    assert outcome.outcome is OutcomeClass.DIRECT_WRONG
    assert outcome.wrong_actions == 1


def test_a_page_that_counted_a_wrong_action_is_wrong_even_beside_an_unjudged_one() -> None:
    outcome = classify_step(truth(), observation(actions=[action(on_target=UNKNOWN)], page_wrong=1))

    assert outcome.outcome is OutcomeClass.DIRECT_WRONG


def test_only_an_unjudged_step_is_classified_ground_truth_unknown() -> None:
    fields = classify_step(truth(), observation(actions=[action(on_target=UNKNOWN)])).model_dump()

    with pytest.raises(ValidationError, match="only an unjudged step"):
        StepOutcome.model_validate({**fields, "unknown_actions": 0})
    with pytest.raises(ValidationError, match="only an unjudged step"):
        StepOutcome.model_validate({**fields, "outcome": OutcomeClass.FAILED})


@given(observations, st.sampled_from(Expectation))
def test_an_unjudged_step_is_never_counted_as_wrong_or_as_correct(
    observed: StepObservation, expectation: Expectation
) -> None:
    outcome = classify_step(truth(expectation=expectation), observed)

    if outcome.outcome is OutcomeClass.GROUND_TRUTH_UNKNOWN:
        assert outcome.unknown_actions > 0
        assert not outcome.outcome.wrong
        assert not outcome.outcome.correct_resolution
        assert outcome.wrong_actions == 0
