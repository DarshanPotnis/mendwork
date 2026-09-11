"""PRESS keys follow a closed grammar so a typo fails at load time."""

import pytest

from mendwork.engine.domain.keys import check_key
from tests.workflows import document, fingerprint, problems, version


@pytest.mark.parametrize(
    "key",
    [
        "Enter",
        "Tab",
        "Escape",
        "ArrowDown",
        "F5",
        "F12",
        "Space",
        "a",
        "Z",
        "7",
        "/",
        "Shift+Tab",
        "Control+a",
        "ControlOrMeta+Shift+k",
        "Alt+F4",
    ],
)
def test_valid_keys_are_accepted(key: str) -> None:
    assert check_key(key) == key


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("Entr", "'Entr' is not a key"),
        ("F13", "'F13' is not a key"),
        (" ", "' ' is not a key"),
        ("+", "'' is not a key"),
        ("ab", "'ab' is not a key"),
        ("Ctrl+a", "'Ctrl' is not a modifier"),
        ("Shift+Shift+a", "a modifier must not repeat"),
        ("Control+", "'' is not a key"),
        ("é", "'é' is not a key"),
    ],
)
def test_invalid_keys_are_explained(key: str, message: str) -> None:
    with pytest.raises(ValueError, match=message.replace("+", r"\+")):
        check_key(key)


def press(**overrides: object) -> dict[str, object]:
    return {
        "id": "submit_search",
        "intent": "Press Enter",
        "action": "press",
        "risk": "safe",
        **overrides,
    }


def test_a_press_step_may_target_an_element_or_the_page() -> None:
    parsed = version(
        steps=[press(key="Enter"), press(id="focused", key="Tab", target=fingerprint())]
    )

    assert [step.id for step in parsed.steps] == ["submit_search", "focused"]


def test_a_press_step_without_a_key_is_rejected() -> None:
    assert problems(document(steps=[press()])) == [("steps[0].key", "is required")]
