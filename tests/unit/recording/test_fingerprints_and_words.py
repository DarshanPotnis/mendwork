"""Fingerprints fitted to the format's bounds, and the words recorded steps are described in."""

import pytest
from pydantic import ValidationError

from mendwork.engine.domain.enums import ActionType, AriaRole
from mendwork.engine.ports.element_types import Box
from mendwork.engine.ports.recording_types import PressKey
from mendwork.engine.recording.describe import (
    navigate_words,
    role_word,
    step_id,
    target_phrase,
    target_words,
)
from mendwork.engine.recording.fingerprints import build_fingerprint, normalized_box
from tests.unit.recording.builders import by_test_id, facts, identity


def test_a_fingerprint_keeps_what_fits_and_drops_what_does_not() -> None:
    fingerprint = build_fingerprint(
        facts(
            "a",
            id="open-reports",
            type="LINK",
            href="https://evil.example/reports?token=1",
            text="x" * 2000,
            label_text="y" * 300,
            nearby_text=("Shipment  reports", "Shipment reports", "", "z" * 300, *"abcdefghij"),
            structural_path=" > ".join(["div"] * 400),
            box=Box(x=0.9, y=-0.2, width=0.5, height=0.123456),
        ),
        identity("link", "View reports", tag="a"),
        [by_test_id("open-reports")],
    )

    assert fingerprint.role is AriaRole.LINK
    assert fingerprint.accessible_name == "View reports"
    assert fingerprint.text is None
    assert fingerprint.label_text is None
    assert fingerprint.attributes.id == "open-reports"
    assert fingerprint.attributes.type == "link"
    assert fingerprint.attributes.href is None
    assert fingerprint.nearby_text == ("Shipment reports", "a", "b", "c", "d", "e", "f", "g")
    assert len(fingerprint.structural_path) <= 1024
    assert fingerprint.structural_path.endswith("div > div")
    assert fingerprint.bbox is not None
    box = fingerprint.bbox
    assert (box.x, box.y, box.width, box.height) == (0.9, 0.0, 0.1, 0.1235)


def test_an_unknown_role_and_an_empty_name_are_left_out() -> None:
    fingerprint = build_fingerprint(
        facts("input", type="password"),
        identity("made-up", "", tag="input"),
        [by_test_id("password")],
    )

    assert fingerprint.role is None
    assert fingerprint.accessible_name is None
    assert fingerprint.attributes.type == "password"


def test_a_name_the_format_cannot_hold_is_an_error() -> None:
    with pytest.raises(ValidationError):
        build_fingerprint(facts(), identity("button", "n" * 300), [by_test_id("save")])


def test_boxes_are_rounded_clamped_and_empty_boxes_dropped() -> None:
    box = normalized_box(Box(x=0.35554, y=0.39026, width=0.28906, height=0.05625))
    assert box is not None
    assert (box.x, box.y, box.width, box.height) == (0.3555, 0.3903, 0.2891, 0.0563)

    edge = normalized_box(Box(x=0.9, y=0.5, width=0.2, height=0.1))
    assert edge is not None
    assert edge.width == pytest.approx(0.1)

    assert normalized_box(Box(x=1.0, y=0.0, width=0.3, height=0.1)) is None
    assert normalized_box(None) is None


@pytest.mark.parametrize(
    ("role", "tag", "word"),
    [
        ("button", "button", "button"),
        ("textbox", "input", "field"),
        ("combobox", "select", "list"),
        (None, "a", "link"),
        (None, "div", "element"),
    ],
)
def test_role_words(role: str | None, tag: str, word: str) -> None:
    assert role_word(role, tag) == word


def test_descriptions_and_intents_for_every_action() -> None:
    assert target_words(
        ActionType.CLICK, role="button", name="Download CSV", tag="button", key=None
    ) == ("CLICK the 'Download CSV' button", "Click the 'Download CSV' button")
    assert target_words(ActionType.FILL, role="textbox", name="From", tag="input", key=None) == (
        "FILL the 'From' field",
        "Fill the 'From' field",
    )
    assert target_words(ActionType.SELECT, role="combobox", name="Country", tag="select", key=None)[
        0
    ] == ("SELECT an option in the 'Country' list")
    assert target_words(
        ActionType.PRESS, role="textbox", name="Password", tag="input", key=PressKey.ENTER
    ) == ("PRESS Enter on the 'Password' field", "Press Enter on the 'Password' field")
    assert target_words(ActionType.PRESS, role=None, name=None, tag="", key=PressKey.ESCAPE) == (
        "PRESS Escape",
        "Press Escape",
    )
    assert target_words(ActionType.PRESS, role="button", name="", tag="button", key=None)[0] == (
        "PRESS a key on the unnamed button"
    )
    with pytest.raises(ValueError, match="no target"):
        target_words(ActionType.NAVIGATE, role=None, name=None, tag="", key=None)


def test_long_names_and_urls_are_shortened() -> None:
    assert target_phrase("button", "word " * 40, "button").endswith("…' button")
    description, intent = navigate_words("https://portal.example.test/" + "p" * 300, "")
    assert description.startswith("NAVIGATE to https://portal.example.test/")
    assert intent.startswith("Open /ppp")
    assert len(intent) <= 170
    assert navigate_words("http://127.0.0.1:8765/index.html", "Sign in · Harborline") == (
        "NAVIGATE to http://127.0.0.1:8765/index.html",
        "Open the 'Sign in · Harborline' page",
    )
    assert navigate_words("http://127.0.0.1:8765", " ")[1] == "Open /"


def test_step_ids_are_unique_slugs() -> None:
    taken = {"click_download_csv"}

    assert step_id("click", "Download CSV", set()) == "click_download_csv"
    assert step_id("click", "Download CSV", taken) == "click_download_csv_2"
    assert step_id("open", "Sign in · Harborline Supply", taken) == "open_sign_in_harborline_supply"
    assert step_id("fill", "Contraseña", set()) == "fill_contrase_a"
    assert step_id("click", "", set()) == "click"
    long = step_id("click", "word " * 40, set())
    assert len(long) <= 64
