"""Heal changes: the record a verified heal leaves, and the child version it creates (ADR 0013)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.changes import HealChange, describe_change
from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.lineage import heal_version
from mendwork.engine.domain.steps import step_target
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import WorkflowValidationError
from tests.fakes.clock import FakeClock
from tests.heal_changes import APPROVAL, heal_change, renamed_target, target_of
from tests.strategies import workflow_versions
from tests.workflows import (
    CREATED_AT_DATETIME,
    click_step,
    document,
    fill_step,
    navigate_step,
    problems,
    version,
)

CODEC = WorkflowYamlCodec(max_bytes=1 << 20)
MODEL_USAGE = {
    "provider": "ollama",
    "model": "qwen3:4b-instruct-2507-q4_K_M",
    "prompt_version": "choose-candidate/1",
    "calls": 2,
    "input_tokens": 458,
    "output_tokens": 84,
    "estimated_cost_usd": "0.000123",
    "confidence": 0.9,
}


def test_a_verified_heal_creates_a_child_that_changes_only_the_healed_target() -> None:
    parent = version()
    snapshot = parent.model_copy(deep=True)
    later = CREATED_AT_DATETIME + timedelta(days=4)
    change = heal_change(parent)

    child = heal_version(parent, change, clock=FakeClock(later))

    assert parent == snapshot
    assert (child.version, child.parent_version, child.created_at) == (2, 1, later)
    assert child.change == change
    assert [step.id for step in child.steps] == [step.id for step in parent.steps]
    assert child.steps[:2] == parent.steps[:2]
    assert step_target(child.steps[2]) == change.new_target
    assert child.steps[2].model_dump(exclude={"target"}) == parent.steps[2].model_dump(
        exclude={"target"}
    )
    assert (child.inputs, child.secrets) == (parent.inputs, parent.secrets)


def test_a_healed_version_survives_the_workflow_file_round_trip() -> None:
    parent = version()
    child = heal_version(parent, heal_change(parent), clock=FakeClock(CREATED_AT_DATETIME))

    assert CODEC.decode(CODEC.encode(child), source="v0002.yaml") == child


def test_a_model_heal_keeps_its_usage_and_cost_through_the_file_round_trip() -> None:
    parent = version()
    change = heal_change(
        parent,
        rung=3,
        score=None,
        margin=None,
        model=MODEL_USAGE,
        strength="weak",
        checkpoints=["url_matches"],
    )

    decoded = CODEC.decode(
        CODEC.encode(heal_version(parent, change, clock=FakeClock(CREATED_AT_DATETIME))),
        source="v0002.yaml",
    )

    assert isinstance(decoded.change, HealChange)
    assert decoded.change.model is not None
    assert decoded.change.model.estimated_cost_usd == Decimal("0.000123")
    assert decoded.change == change


def test_describe_change_names_the_healed_step_and_rung() -> None:
    assert describe_change(heal_change(version())) == "healed step save at rung 2"


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"step_id": "missing"}, "v1 of demo has no step missing"),
        ({"step_id": "open_portal"}, "step open_portal has no target to heal"),
    ],
)
def test_a_heal_for_a_step_that_cannot_take_it_is_refused(
    update: dict[str, object], message: str
) -> None:
    parent = version()
    change = heal_change(parent).model_copy(update=update)

    with pytest.raises(WorkflowValidationError, match=message):
        heal_version(parent, change, clock=FakeClock(CREATED_AT_DATETIME))


def test_a_heal_verified_against_another_target_is_refused() -> None:
    parent = version()
    other = renamed_target(target_of(parent, "save"), "Save draft")
    change = heal_change(parent).model_copy(update={"old_target": other})

    with pytest.raises(WorkflowValidationError, match="verified against a different target"):
        heal_version(parent, change, clock=FakeClock(CREATED_AT_DATETIME))


def test_an_irreversible_step_is_healed_only_with_the_approval_that_let_it_act() -> None:
    parent = version(steps=[navigate_step(), fill_step(), click_step(risk="irreversible")])
    assert parent.steps[2].risk is RiskLevel.IRREVERSIBLE
    clock = FakeClock(CREATED_AT_DATETIME)

    with pytest.raises(WorkflowValidationError, match="needs the approval that let it act"):
        heal_version(parent, heal_change(parent), clock=clock)

    child = heal_version(parent, heal_change(parent, approval=APPROVAL), clock=clock)
    assert isinstance(child.change, HealChange)
    assert child.change.approval is not None
    assert child.change.approval.audit_sequence == 3


def test_a_heal_that_would_type_a_literal_into_a_credential_field_is_refused() -> None:
    parent = version()
    old = target_of(parent, "fill_name")
    password = old.model_copy(
        update={"attributes": old.attributes.model_copy(update={"type": "password"})}
    )

    with pytest.raises(WorkflowValidationError, match="cannot use the healed target") as caught:
        heal_version(
            parent,
            heal_change(parent, "fill_name", new_target=password),
            clock=FakeClock(CREATED_AT_DATETIME),
        )

    assert any("secret reference" in issue.message for issue in caught.value.issues)


def test_a_heal_change_must_change_the_target() -> None:
    parent = version()

    with pytest.raises(ValidationError, match="a heal changes the step's target"):
        heal_change(parent, new_target=target_of(parent, "save"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"model": MODEL_USAGE}, "only a rung 3 heal records model usage"),
        ({"strength": "none"}, "verified by at least one checkpoint that can prove it"),
    ],
)
def test_a_heal_change_must_describe_a_verified_change(
    overrides: dict[str, object], message: str
) -> None:
    valid = heal_change(version()).model_dump(mode="json")

    with pytest.raises(ValidationError, match=message):
        HealChange.model_validate({**valid, **overrides})


def test_a_stored_version_whose_heal_does_not_match_its_step_is_refused() -> None:
    parent = version()
    change = heal_change(parent).model_dump(mode="json")

    found = problems(document(version=2, parent_version=1, change=change))

    assert any("is not the target step save has in this version" in text for _, text in found)


@given(workflow_versions(), st.integers(min_value=1, max_value=10_000))
def test_every_heal_keeps_the_lineage_invariants(parent: WorkflowVersion, number: int) -> None:
    targeted = [step for step in parent.steps if step_target(step) is not None]
    assume(targeted)
    healed = targeted[0]
    new = renamed_target(target_of(parent, healed.id), f"Renamed control {number}")
    assume(new != target_of(parent, healed.id))
    approval = APPROVAL if healed.risk is RiskLevel.IRREVERSIBLE else None
    change = heal_change(parent, healed.id, new_target=new, approval=approval)
    snapshot = parent.model_copy(deep=True)

    child = heal_version(parent, change, clock=FakeClock(CREATED_AT_DATETIME))

    assert parent == snapshot
    assert [step.id for step in child.steps] == [step.id for step in parent.steps]
    for before, after in zip(parent.steps, child.steps, strict=True):
        if before.id != healed.id:
            assert after == before
    assert target_of(child, healed.id) == new
    assert CODEC.decode(CODEC.encode(child), source="child.yaml") == child
