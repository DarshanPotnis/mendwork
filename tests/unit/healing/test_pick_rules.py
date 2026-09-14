"""The rules only a model's pick faces: the context veto, and the bar a pick must clear on a step
whose checkpoints are weak."""

from typing import Final

import pytest

from mendwork.engine.domain.checkpoints import Checkpoint
from mendwork.engine.domain.heals import RejectionReason
from mendwork.engine.domain.steps import Step
from mendwork.engine.healing.pick_rules import (
    context_rejection,
    surviving_identity_attributes,
    verification_rejection,
)
from mendwork.engine.safety.heal_policy import VerificationStrength, verification_strength
from tests.unit.healing.builders import export_button, live, reference_field, step
from tests.unit.replay.builders import healing
from tests.workflows import click_step

CONFIG = healing()
EXPORT = export_button()


def test_a_pick_sharing_none_of_the_recorded_nearby_text_is_refused() -> None:
    elsewhere = live(EXPORT, facts={"nearby_text": ("Main navigation",)})
    bare = live(EXPORT, facts={"nearby_text": ()})

    for candidate in (elsewhere, bare):
        rejection = context_rejection(EXPORT, candidate, CONFIG)
        assert rejection is not None
        assert rejection.reason is RejectionReason.CONTEXT_LOST


def test_a_pick_keeping_some_recorded_context_is_not_vetoed() -> None:
    near = live(EXPORT, facts={"nearby_text": ("Quarterly ledger", "Totals")})

    assert context_rejection(EXPORT, near, CONFIG) is None


def test_without_recorded_nearby_text_there_is_nothing_to_veto_on() -> None:
    unrecorded = export_button(nearby_text=[])

    assert (
        context_rejection(unrecorded, live(unrecorded, facts={"nearby_text": ()}), CONFIG) is None
    )


def test_surviving_identity_attributes_leave_out_where_a_link_goes() -> None:
    link = export_button(
        tag="a",
        role="link",
        attributes={"id": "open-ledger", "href": "/ledger", "data_testid": "ledger-open"},
    )
    same_destination = live(
        link, facts={"id": "nav-ledger", "data_testid": None, "href": "/ledger"}
    )
    kept_test_id = live(link, facts={"id": "renamed", "href": "/ledger"})

    assert surviving_identity_attributes(link, same_destination) == ()
    assert surviving_identity_attributes(link, kept_test_id) == ("data_testid",)


def test_field_attributes_and_labels_compare_after_normalization() -> None:
    field = reference_field(
        attributes={
            "id": "customer-reference",
            "name": "reference",
            "type": "text",
            "autocomplete": "off",
            "placeholder": "Customer reference",
            "aria_label": "Reference",
        }
    )
    candidate = live(
        field,
        facts={"id": "ref-2", "placeholder": "customer  REFERENCE", "aria_label": "Other"},
    )

    assert surviving_identity_attributes(field, candidate) == (
        "name",
        "autocomplete",
        "placeholder",
    )


@pytest.mark.parametrize(
    ("checkpoints", "strength"),
    [
        (
            [{"kind": "url_matches", "mode": "prefix", "pattern": "https://ledger.example.test/"}],
            "weak",
        ),
        (
            [
                {"kind": "url_matches", "mode": "prefix", "pattern": "https://l.example.test/"},
                {"kind": "no_error_banner"},
            ],
            "weak",
        ),
        ([{"kind": "text_present", "text": "Exported"}], "strong"),
        (
            [
                {"kind": "url_matches", "mode": "prefix", "pattern": "https://l.example.test/"},
                {"kind": "element_visible", "selector": {"strategy": "css", "value": "h1"}},
            ],
            "strong",
        ),
        ([{"kind": "download_completed", "filename_pattern": ".*"}], "strong"),
        ([{"kind": "no_error_banner"}], "none"),
        ([], "none"),
    ],
)
def test_checkpoint_strength_is_strong_when_any_checkpoint_observes_an_effect(
    checkpoints: list[dict[str, object]], strength: str
) -> None:
    parsed: tuple[Checkpoint, ...] = step(click_step(checkpoints=checkpoints)).checkpoints

    assert verification_strength(parsed) is VerificationStrength(strength)


def test_a_fill_proven_only_by_its_value_is_weak() -> None:
    fill = step(
        {
            "id": "reference",
            "intent": "Fill the reference",
            "action": "fill",
            "risk": "caution",
            "target": reference_field().model_dump(mode="json"),
            "value": {"kind": "literal", "value": "R-1"},
            "checkpoints": [{"kind": "field_has_value"}],
        }
    )

    assert verification_strength(fill.checkpoints) is VerificationStrength.WEAK


WEAK: Final[list[dict[str, object]]] = [
    {"kind": "url_matches", "mode": "prefix", "pattern": "https://ledger.example.test/"}
]
STRONG: Final[list[dict[str, object]]] = [{"kind": "text_present", "text": "Ledger exported"}]
LINK: Final = export_button(
    tag="a",
    role="link",
    attributes={"id": "open-ledger", "href": "/ledger", "data_testid": "ledger-open"},
)


def link_step(checkpoints: list[dict[str, object]]) -> Step:
    return step(click_step(target=LINK.model_dump(mode="json"), checkpoints=checkpoints))


def test_a_pick_keeping_no_identifier_on_a_weakly_verified_step_is_refused() -> None:
    same_destination = live(LINK, facts={"id": "nav-ledger", "data_testid": None})

    rejection = verification_rejection(link_step(WEAK), LINK, same_destination)

    assert rejection is not None
    assert rejection.reason is RejectionReason.WEAK_VERIFICATION


def test_strong_checkpoints_need_no_surviving_identifier() -> None:
    same_destination = live(LINK, facts={"id": "nav-ledger", "data_testid": None})

    assert verification_rejection(link_step(STRONG), LINK, same_destination) is None


@pytest.mark.parametrize(
    "kept", [{"id": "open-ledger"}, {"data_testid": "ledger-open"}], ids=["id", "test id"]
)
def test_one_kept_identifier_clears_the_weak_verification_bar(kept: dict[str, object]) -> None:
    candidate = live(LINK, facts={"id": "renamed", "data_testid": None, **kept})

    assert verification_rejection(link_step(WEAK), LINK, candidate) is None


def test_a_fill_proven_by_its_value_clears_the_bar_with_its_name_but_not_its_purpose() -> None:
    field = reference_field(
        attributes={
            "id": "customer-reference",
            "name": "reference",
            "type": "text",
            "autocomplete": "off",
            "placeholder": "Customer reference",
            "aria_label": "Reference",
        }
    )
    fill = step(
        {
            "id": "reference",
            "intent": "Fill the reference",
            "action": "fill",
            "risk": "caution",
            "target": field.model_dump(mode="json"),
            "value": {"kind": "literal", "value": "R-1"},
            "checkpoints": [{"kind": "field_has_value"}],
        }
    )
    kept_name = live(field, facts={"id": "ref-2"})
    kept_purpose = live(field, facts={"id": "ref-2", "name": "ref"})

    assert verification_rejection(fill, field, kept_name) is None
    rejection = verification_rejection(fill, field, kept_purpose)
    assert rejection is not None
    assert rejection.reason is RejectionReason.WEAK_VERIFICATION


def test_a_step_with_no_proving_checkpoint_faces_the_bar_too() -> None:
    same_destination = live(LINK, facts={"id": "nav-ledger", "data_testid": None})

    rejection = verification_rejection(
        link_step([{"kind": "no_error_banner"}]), LINK, same_destination
    )

    assert rejection is not None
