"""Heal safety rules as tables: kinds, danger, identifiers, credentials, authentication."""

import pytest

from mendwork.engine.domain.enums import ActionType, RiskLevel
from mendwork.engine.domain.steps import FillStep, Step
from mendwork.engine.safety.heal_kinds import (
    ElementKind,
    InteractionClass,
    action_accepts,
    compare_kinds,
    interaction_class,
)
from mendwork.engine.safety.heal_policy import (
    FailedHealRecovery,
    authentication_step,
    credential_mismatch,
    heal_may_act,
    identifier_mismatch,
    identifier_tokens,
    introduced_danger,
    is_credential_step,
    is_verifiable,
    recovery_after_failed_heal,
)
from tests.unit.healing.builders import step
from tests.unit.recording.builders import VOCABULARY
from tests.workflows import click_step, field_fingerprint, fill_step, fingerprint, secret_ref

C = InteractionClass
SESSION_REASON = 'changes the session: "log in"'
URL_CHECK = {"kind": "url_matches", "mode": "prefix", "pattern": "https://ledger.example.test/"}


def kind(
    tag: str,
    role: str | None = None,
    input_type: str | None = None,
    *,
    has_href: bool = False,
    text_entry: bool = False,
) -> ElementKind:
    return ElementKind(
        tag=tag, role=role, input_type=input_type, has_href=has_href, text_entry=text_entry
    )


@pytest.mark.parametrize(
    ("element", "expected"),
    [
        (kind("button", "button", "submit"), C.SUBMIT),
        (kind("button", "button"), C.SUBMIT),  # a button without a type submits its form
        (kind("input", "button", "image"), C.SUBMIT),
        (kind("button", "button", "button"), C.ACTIVATE),
        (kind("input", "button", "button"), C.ACTIVATE),
        (kind("a", "link", has_href=True), C.ACTIVATE),
        (kind("div", "button"), C.ACTIVATE),
        (kind("li", "menuitem"), C.ACTIVATE),
        (kind("a"), C.OTHER),
        (kind("input", "checkbox", "checkbox"), C.TOGGLE),
        (kind("div", "switch"), C.TOGGLE),
        (kind("input", "textbox", "email"), C.TEXT_ENTRY),
        (kind("input", None, "password"), C.TEXT_ENTRY),
        (kind("textarea", "textbox"), C.TEXT_ENTRY),
        (kind("div", None, text_entry=True), C.TEXT_ENTRY),
        (kind("div", "searchbox"), C.TEXT_ENTRY),
        (kind("input", "textbox", "date"), C.DATE_LIKE),
        (kind("select", "combobox"), C.CHOICE),
        (kind("div", "listbox"), C.CHOICE),
        (kind("input", "button", "reset"), C.OTHER),
        (kind("input", None, "file"), C.OTHER),
    ],
)
def test_every_control_has_one_interaction_class(
    element: ElementKind, expected: InteractionClass
) -> None:
    assert interaction_class(element) is expected


@pytest.mark.parametrize(
    ("action", "accepted"),
    [
        (ActionType.CLICK, {C.SUBMIT, C.ACTIVATE, C.TOGGLE, C.OTHER}),
        (ActionType.PRESS, set(C)),
        (ActionType.FILL, {C.TEXT_ENTRY, C.DATE_LIKE}),
        (ActionType.SELECT, {C.CHOICE}),
        (ActionType.NAVIGATE, set()),
    ],
)
def test_each_action_accepts_only_the_classes_it_can_act_on(
    action: ActionType, accepted: set[InteractionClass]
) -> None:
    assert {cls for cls in C if action_accepts(action, cls)} == accepted


def test_a_button_that_became_a_link_is_allowed_only_with_an_effect_checkpoint() -> None:
    comparison = compare_kinds(kind("button", "button", "button"), kind("a", "link", has_href=True))

    assert comparison.compatible
    assert comparison.change == "button → link"
    assert comparison.needs_effect_checkpoint


def test_a_link_that_became_a_button_is_allowed_the_same_way() -> None:
    comparison = compare_kinds(kind("a", "link", has_href=True), kind("button", "button", "button"))

    assert (comparison.compatible, comparison.change, comparison.needs_effect_checkpoint) == (
        True,
        "link → button",
        True,
    )


def test_a_submit_button_never_becomes_a_link() -> None:
    comparison = compare_kinds(kind("button", "button", "submit"), kind("a", "link", has_href=True))

    assert not comparison.compatible
    assert comparison.reason is not None
    assert "submit a form" in comparison.reason


def test_an_activator_never_becomes_a_control_that_submits_a_form() -> None:
    comparison = compare_kinds(
        kind("button", "button", "button"), kind("button", "button", "submit")
    )

    assert not comparison.compatible


def test_a_toggle_never_stands_in_for_a_button() -> None:
    assert not compare_kinds(
        kind("button", "button", "button"), kind("input", "checkbox", "checkbox")
    ).compatible
    assert not compare_kinds(
        kind("input", "checkbox", "checkbox"), kind("input", "radio", "radio")
    ).compatible


def test_a_field_recorded_without_a_role_is_compared_by_tag_and_type() -> None:
    # Date and password inputs are recorded without a role; the page reports textbox.
    date = compare_kinds(kind("input", None, "date"), kind("input", "textbox", "date"))
    other_date = compare_kinds(kind("input", None, "date"), kind("input", "textbox", "time"))

    assert (date.compatible, date.change) == (True, None)
    assert not other_date.compatible


def test_text_fields_may_change_type_and_identical_controls_need_nothing() -> None:
    assert compare_kinds(
        kind("input", "textbox", "text"), kind("input", "textbox", "email")
    ).compatible
    same = compare_kinds(kind("button", "button", "button"), kind("button", "button", "button"))
    assert (same.compatible, same.change, same.needs_effect_checkpoint) == (True, None, False)
    assert compare_kinds(kind("input", None, "file"), kind("input", None, "file")).compatible


@pytest.mark.parametrize(
    ("recorded", "found", "introduced"),
    [
        (["Export ledger"], ["Delete ledger"], ("delete",)),
        (["Export ledger"], ["Export ledger", "Purge everything"], ("purge",)),
        (["Delete draft"], ["Delete this draft"], ()),
        (["Remove filter"], ["Remove filter"], ()),
        (["Export ledger"], ["Remove filter"], ()),
        (["Export ledger"], [None, "Share ledger"], ()),
    ],
)
def test_danger_is_what_a_candidate_adds_to_the_recorded_names(
    recorded: list[str | None], found: list[str | None], introduced: tuple[str, ...]
) -> None:
    assert introduced_danger(recorded, found, VOCABULARY) == introduced


@pytest.mark.parametrize(
    ("recorded", "found", "mismatch"),
    [
        (["Open invoice INV-2231"], ["Open invoice INV-2231"], None),
        (["Open invoice INV-2231"], ["Show invoice INV-2231"], None),
        (["Open invoice INV-2231"], ["Open invoice"], None),
        (["Open invoice INV-2231"], ["Open invoice INV-2231 line 2"], None),
        (["Open invoice"], ["Open invoice page 2"], None),
        (["Open invoice INV-2231"], ["Open invoice INV-7780"], (("2231",), ("7780",))),
    ],
)
def test_a_different_identifier_marks_another_rows_control(
    recorded: list[str | None],
    found: list[str | None],
    mismatch: tuple[tuple[str, ...], tuple[str, ...]] | None,
) -> None:
    assert identifier_mismatch(recorded, found) == mismatch


def test_identifier_tokens_are_the_numbers_in_a_name() -> None:
    assert identifier_tokens(["Invoice INV-2231 for Q3", None]) == frozenset({"2231", "3"})


def test_a_credential_goes_only_into_a_masked_field_and_plain_text_never_does() -> None:
    assert not credential_mismatch(credential_step=True, found_masked=True)
    assert not credential_mismatch(credential_step=False, found_masked=False)
    assert credential_mismatch(credential_step=True, found_masked=False)
    assert credential_mismatch(credential_step=False, found_masked=True)


def _secret_fill() -> Step:
    target = field_fingerprint(
        accessible_name="Vault passcode",
        label_text="Vault passcode",
        attributes={"id": "vault-passcode", "type": "password"},
    )
    return step(fill_step(target=target, value=secret_ref("vault_passcode")))


def test_a_fill_is_a_credential_step_when_it_types_a_secret() -> None:
    secret_fill = _secret_fill()
    plain_fill = step(fill_step())

    assert isinstance(secret_fill, FillStep)
    assert isinstance(plain_fill, FillStep)
    assert is_credential_step(secret_fill)
    assert not is_credential_step(plain_fill)


@pytest.mark.parametrize(
    ("document", "submits_password_form", "reason"),
    [
        (click_step(target=fingerprint(accessible_name="Log in")), False, SESSION_REASON),
        (
            click_step(target=fingerprint(accessible_name="Continue")),
            True,
            "submits a form holding a password (a sign-in)",
        ),
        (click_step(target=fingerprint(accessible_name="Continue")), False, None),
        (fill_step(), False, None),
        (
            {"id": "go", "intent": "Go", "action": "navigate", "risk": "safe",
             "value": {"kind": "literal", "value": "https://ledger.example.test/"}},
            True,
            None,
        ),
        (
            {"id": "enter", "intent": "Press Enter", "action": "press", "risk": "caution",
             "key": "Enter"},
            True,
            None,
        ),
    ],
)  # fmt: skip
def test_authentication_steps_are_the_classifiers_session_steps(
    document: dict[str, object], submits_password_form: bool, reason: str | None
) -> None:
    found = authentication_step(
        step(document), VOCABULARY, submits_password_form=submits_password_form
    )

    assert found == reason


def test_filling_a_credential_is_an_authentication_step() -> None:
    assert authentication_step(_secret_fill(), VOCABULARY, submits_password_form=False) == (
        "fills a credential"
    )


@pytest.mark.parametrize(
    ("document", "verifiable"),
    [
        (click_step(checkpoints=[URL_CHECK]), True),
        (click_step(checkpoints=[{"kind": "text_present", "text": "Exported"}]), True),
        (click_step(checkpoints=[{"kind": "no_error_banner"}]), False),
        (click_step(), False),
        (fill_step(checkpoints=[{"kind": "field_has_value"}]), True),
        (fill_step(), False),
        (
            {"id": "pick", "intent": "Pick", "action": "select", "risk": "caution",
             "target": fingerprint(tag="select", role="combobox", accessible_name="Currency"),
             "value": {"kind": "literal", "value": "EUR"},
             "checkpoints": [{"kind": "text_present", "text": "EUR"}]},
            True,
        ),
    ],
)  # fmt: skip
def test_a_heal_needs_a_checkpoint_that_can_prove_it(
    document: dict[str, object], verifiable: bool
) -> None:
    assert is_verifiable(step(document)) is verifiable


def test_only_irreversible_steps_are_kept_from_acting_on_a_heal() -> None:
    assert [heal_may_act(risk) for risk in RiskLevel] == [True, True, False]


def test_recovery_restores_safe_steps_resets_caution_steps_and_never_retries_irreversible() -> None:
    assert [recovery_after_failed_heal(risk) for risk in RiskLevel] == [
        FailedHealRecovery.RESTORE,
        FailedHealRecovery.RESET_THEN_RESTORE,
        FailedHealRecovery.NEEDS_REVIEW,
    ]
