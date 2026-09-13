"""Rung 1's alternate selectors, candidate compatibility and signatures, and safety checks."""

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.heals import RejectionReason
from mendwork.engine.healing.alternates import alternate_selectors
from mendwork.engine.healing.candidates import candidate_signature, compatible
from mendwork.engine.healing.checks import mask_selector, safety_rejection
from mendwork.engine.ports.element_types import Box, ElementFacts
from tests.unit.healing.builders import (
    EXPORT_ROLE,
    EXPORT_TEST_ID,
    export_button,
    live,
    reference_field,
    step,
)
from tests.unit.recording.builders import VOCABULARY
from tests.unit.replay.builders import selector
from tests.workflows import click_step, fill_step, input_ref, secret_ref

URL_CHECK = {"kind": "url_matches", "mode": "prefix", "pattern": "https://ledger.example.test/"}


def test_alternates_derive_every_unrecorded_selector_from_the_fingerprint() -> None:
    recorded = export_button(text="Export")

    found = alternate_selectors(recorded)

    assert EXPORT_TEST_ID not in found
    assert EXPORT_ROLE not in found
    assert found == (
        selector(strategy="role_name", role="button", name="Export", exact=False),
        selector(strategy="text", value="Export"),
        selector(strategy="css", value="#export-ledger"),
    )


def test_fields_get_label_and_placeholder_alternates_but_no_text_selector() -> None:
    recorded = reference_field(
        selectors=[selector(strategy="css", value="#customer-reference").model_dump()],
        attributes={"id": "customer-reference", "placeholder": "REF-000"},
        text="Customer reference",
    )

    assert alternate_selectors(recorded) == (
        selector(strategy="role_name", role="textbox", name="Customer reference"),
        selector(strategy="label", value="Customer reference"),
        selector(strategy="placeholder", value="REF-000"),
    )


def test_alternates_are_tried_inside_every_recorded_scope() -> None:
    row = selector(strategy="role_name", role="row", name="Invoice 7", exact=False)
    scoped = selector(strategy="css", value="#export-ledger", within=row.model_dump())
    recorded = export_button(selectors=[scoped.model_dump()], attributes={"id": "export-ledger"})

    found = alternate_selectors(recorded)

    assert selector(strategy="role_name", role="button", name="Export ledger") in found
    assert (
        selector(strategy="role_name", role="button", name="Export ledger", within=row.model_dump())
        in found
    )
    assert scoped not in found


def test_a_text_too_long_for_a_selector_is_not_proposed() -> None:
    recorded = export_button(text="x" * 200, accessible_name=None, role=None)

    assert all(alternate.strategy != "text" for alternate in alternate_selectors(recorded))


def test_candidates_are_filtered_by_what_the_action_can_act_on() -> None:
    button = live(export_button())
    field = live(reference_field())

    assert compatible(ActionType.CLICK, button)
    assert not compatible(ActionType.CLICK, field)
    assert compatible(ActionType.FILL, field)
    assert not compatible(ActionType.FILL, button)


def test_signatures_ignore_the_element_ref_but_not_what_the_page_shows() -> None:
    recorded = export_button()

    assert candidate_signature(live(recorded, "e1")) == candidate_signature(live(recorded, "e9"))
    moved = live(recorded, facts={"box": Box(x=0.1, y=0.1, width=0.12, height=0.05)})
    assert candidate_signature(moved) != candidate_signature(live(recorded))
    assert candidate_signature(live(recorded, facts={"box": None}))[-1] == ""


def test_a_candidate_that_adds_a_danger_word_is_refused_first() -> None:
    recorded = export_button()
    candidate = live(recorded, identity={"name": "Delete ledger 7"})

    rejection = safety_rejection(
        step(click_step(target=recorded.model_dump())), recorded, candidate, VOCABULARY
    )

    assert rejection is not None
    assert rejection.reason is RejectionReason.DANGER_WORD
    assert '"delete"' in rejection.detail


def test_a_candidate_naming_another_identifier_is_refused() -> None:
    recorded = export_button(accessible_name="Export ledger 2025", text="Export ledger 2025")
    candidate = live(
        recorded, identity={"name": "Export ledger 2026"}, facts={"text": "Export ledger 2026"}
    )

    rejection = safety_rejection(
        step(click_step(target=recorded.model_dump())), recorded, candidate, VOCABULARY
    )

    assert rejection is not None
    assert rejection.reason is RejectionReason.IDENTIFIER_MISMATCH


def test_a_change_of_kind_is_refused_without_an_effect_checkpoint_and_allowed_with_one() -> None:
    recorded = export_button()
    link = live(
        recorded,
        identity={"tag": "a", "role": "link", "input_type": None},
        facts={"href": "/ledger"},
    )

    unverified = step(click_step(target=recorded.model_dump()))
    verified = step(click_step(target=recorded.model_dump(), checkpoints=[URL_CHECK]))

    refused = safety_rejection(unverified, recorded, link, VOCABULARY)
    assert refused is not None
    assert refused.reason is RejectionReason.KIND_CHANGED
    assert "button → link" in refused.detail
    assert safety_rejection(verified, recorded, link, VOCABULARY) is None


def test_an_incompatible_kind_is_refused_with_its_reason() -> None:
    recorded = export_button()
    toggle = live(recorded, identity={"tag": "input", "role": "checkbox", "input_type": "checkbox"})

    rejection = safety_rejection(
        step(click_step(target=recorded.model_dump())), recorded, toggle, VOCABULARY
    )

    assert rejection is not None
    assert (rejection.reason, "flip a state" in rejection.detail) == (
        RejectionReason.KIND_CHANGED,
        True,
    )


def test_credential_fills_go_only_into_masked_fields_that_can_be_masked() -> None:
    recorded = reference_field(
        attributes={"id": "vault-passcode", "type": "password"},
        accessible_name="Vault passcode",
        label_text="Vault passcode",
    )
    secret = step(fill_step(target=recorded.model_dump(), value=secret_ref("vault_passcode")))
    plain = step(fill_step(target=reference_field().model_dump(), value=input_ref("reference")))

    visible = live(recorded, facts={"masked": False})
    masked_without_selector = live(
        recorded, facts={"masked": True, "id": None, "name": None, "data_testid": None}
    )
    masked_plain = live(reference_field(), facts={"masked": True})

    shown = safety_rejection(secret, recorded, visible, VOCABULARY)
    unmaskable = safety_rejection(secret, recorded, masked_without_selector, VOCABULARY)
    typed_plain = safety_rejection(plain, reference_field(), masked_plain, VOCABULARY)
    assert shown is not None
    assert "shows what is typed" in shown.detail
    assert unmaskable is not None
    assert "mask" in unmaskable.detail
    assert typed_plain is not None
    assert typed_plain.reason is RejectionReason.CREDENTIAL_MISMATCH
    assert safety_rejection(secret, recorded, live(recorded), VOCABULARY) is None
    assert safety_rejection(plain, reference_field(), live(reference_field()), VOCABULARY) is None


def test_a_mask_selector_uses_the_test_id_then_a_stable_id_or_name() -> None:
    facts = ElementFacts(tag="input", structural_path="form > input")

    assert mask_selector(facts.model_copy(update={"data_testid": "code"})) == selector(
        strategy="test_id", value="code"
    )
    assert mask_selector(facts.model_copy(update={"id": "code"})) == selector(
        strategy="css", value="#code"
    )
    assert mask_selector(facts.model_copy(update={"name": "code"})) == selector(
        strategy="css", value='input[name="code"]'
    )
    assert mask_selector(facts) is None
    assert mask_selector(facts.model_copy(update={"data_testid": "x" * 300})) is None
