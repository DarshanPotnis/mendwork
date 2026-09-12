"""The Rung 0 identity check: what counts as the same element, and what is a drifted match."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.replay.identity import (
    Difference,
    effective_type,
    identity_differences,
    normalize_name,
    recorded_identity,
    same_identity,
)
from tests.unit.replay.builders import TEST_ID, button_fingerprint, password_fingerprint


def found(**fields: object) -> ElementIdentity:
    values: dict[str, object] = {
        "tag": "button",
        "role": "button",
        "name": "Download CSV",
        "confirmed": True,
    }
    return ElementIdentity.model_validate({**values, **fields})


def test_the_same_role_and_name_is_the_same_element() -> None:
    assert identity_differences(button_fingerprint(), found()) == ()


FULLWIDTH_NAME = "\uff24\uff4f\uff57\uff4e\uff4c\uff4f\uff41\uff44 \uff23\uff33\uff36"


@pytest.mark.parametrize(
    "seen",
    ["download csv", "  Download\u00a0\u00a0CSV \n", "DOWNLOAD CSV", FULLWIDTH_NAME],
)
def test_names_compare_after_nfkc_case_folding_and_whitespace_collapsing(seen: str) -> None:
    assert identity_differences(button_fingerprint(), found(name=seen)) == ()


def test_case_folding_is_unicode_aware() -> None:
    assert normalize_name("Straße") == normalize_name("STRASSE")


def test_a_different_name_is_a_drifted_match() -> None:
    assert identity_differences(button_fingerprint(), found(name="Delete data")) == (
        Difference.NAME,
    )


def test_a_different_role_is_a_drifted_match() -> None:
    assert identity_differences(button_fingerprint(), found(tag="a", role="link")) == (
        Difference.ROLE,
    )


def test_an_identity_playwright_could_not_confirm_is_a_drifted_match() -> None:
    assert identity_differences(button_fingerprint(), found(confirmed=False)) == (
        Difference.UNCONFIRMED,
    )


def test_role_less_fingerprints_compare_tag_and_type_instead_of_role() -> None:
    fingerprint = password_fingerprint((TEST_ID,))
    # Playwright gives a password input the textbox role; the fingerprint records none.
    element = found(tag="input", input_type="password", role="textbox", name="Password")

    assert identity_differences(fingerprint, element) == ()


def test_a_role_less_field_that_changed_type_is_a_drifted_match() -> None:
    fingerprint = password_fingerprint((TEST_ID,))
    element = found(tag="input", input_type="text", role="textbox", name="Password")

    assert identity_differences(fingerprint, element) == (Difference.INPUT_TYPE,)


def test_a_role_less_field_that_changed_tag_is_a_drifted_match() -> None:
    fingerprint = password_fingerprint((TEST_ID,))
    element = found(tag="textarea", input_type=None, role="textbox", name="Password")

    assert identity_differences(fingerprint, element) == (Difference.TAG, Difference.INPUT_TYPE)


def test_a_missing_type_attribute_means_the_html_default() -> None:
    fingerprint = Fingerprint(
        tag="input", accessible_name="Name", structural_path="form > input", selectors=(TEST_ID,)
    )

    assert (
        identity_differences(
            fingerprint, found(tag="input", input_type="text", role="textbox", name="Name")
        )
        == ()
    )
    assert effective_type("button", None) == "submit"
    assert effective_type("div", None) is None


def test_every_difference_is_reported_together() -> None:
    element = found(tag="a", role="link", name="Delete data", confirmed=False)

    assert identity_differences(button_fingerprint(), element) == (
        Difference.ROLE,
        Difference.NAME,
        Difference.UNCONFIRMED,
    )


def test_a_missing_recorded_name_equals_an_empty_name() -> None:
    fingerprint = Fingerprint(tag="button", structural_path="main > button", selectors=(TEST_ID,))

    assert identity_differences(fingerprint, found(role=None, name="   ")) == ()


def test_a_page_reading_is_the_same_identity_whatever_its_confirmation() -> None:
    assert same_identity(found(confirmed=True), found(confirmed=None, name="download  csv"))
    assert not same_identity(found(), found(name="Delete data"))


def test_the_recorded_identity_mirrors_the_fingerprint() -> None:
    report = recorded_identity(password_fingerprint((TEST_ID,)))

    assert (report.tag, report.input_type, report.role, report.name) == (
        "input",
        "password",
        None,
        "Password",
    )


@given(st.text())
def test_normalization_is_idempotent(text: str) -> None:
    assert normalize_name(normalize_name(text)) == normalize_name(text)


@given(st.text(alphabet=st.characters(codec="ascii", categories=("L", "N", "Zs"))))
def test_case_and_spacing_alone_never_make_a_drifted_match(text: str) -> None:
    fingerprint = button_fingerprint(name=text) if text.strip() else button_fingerprint()
    recorded = text if text.strip() else "Download CSV"

    element = found(name=f"  {recorded.upper()}  ".replace(" ", "\t  "))

    assert Difference.NAME not in identity_differences(fingerprint, element)
