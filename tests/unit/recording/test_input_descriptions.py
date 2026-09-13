"""Input descriptions: answers split into name and description, and defaults from the recording."""

import pytest

from mendwork.engine.domain.enums import ActionType, InputKind, RiskLevel
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.recording import DraftStep, InputHint, LiteralDraft
from mendwork.engine.recording.input_descriptions import (
    InputAnswer,
    default_input_description,
    description_problem,
    split_answer,
)
from tests.unit.recording.builders import by_test_id


def step(action: ActionType, label: str | None = None, value: str = "x") -> DraftStep:
    target = (
        None
        if action is ActionType.NAVIGATE
        else Fingerprint(
            tag="input",
            label_text=label,
            structural_path="main > input",
            selectors=(by_test_id("field"),),
        )
    )
    return DraftStep(
        index=0,
        step_id="step",
        action=action,
        description="a step",
        intent="a step",
        risk=RiskLevel.CAUTION,
        target=target,
        value=LiteralDraft(value=value),
    )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("", InputAnswer(name="", description=None)),
        ("portal_url", InputAnswer(name="portal_url", description=None)),
        (" portal_url : sign-in page ", InputAnswer(name="portal_url", description="sign-in page")),
        ("portal_url:", InputAnswer(name="portal_url", description=None)),
        (": only a description", InputAnswer(name="", description="only a description")),
        ("url: https://x.test/a", InputAnswer(name="url", description="https://x.test/a")),
    ],
)
def test_answers_split_at_the_first_colon(answer: str, expected: InputAnswer) -> None:
    assert split_answer(answer) == expected


def test_descriptions_must_fit_the_format() -> None:
    assert description_problem("Sign-in page of the portal") is None
    assert description_problem("x" * 257) is not None
    assert description_problem("two\nlines") is not None


def test_url_defaults_name_the_path_never_the_host() -> None:
    start = default_input_description(
        step(ActionType.NAVIGATE),
        "http://127.0.0.1:8765/index.html?seed=3",
        InputKind.URL,
        InputHint.START_URL,
    )
    other = default_input_description(
        step(ActionType.NAVIGATE), "https://portal.example.test", InputKind.URL, None
    )
    long_path = default_input_description(
        step(ActionType.NAVIGATE), "https://portal.example.test/" + "p" * 300, InputKind.URL, None
    )

    assert start == "URL of the page the workflow starts on (recorded at /index.html)"
    assert other == "URL of the page opened at /"
    assert "127.0.0.1" not in start
    assert long_path.endswith("…")
    assert len(long_path) <= 256


@pytest.mark.parametrize(
    ("action", "label", "kind", "hint", "expected"),
    [
        (ActionType.FILL, "Email address", InputKind.TEXT, InputHint.EMAIL,
         "Email address typed into the 'Email address' field"),
        (ActionType.FILL, "Login", InputKind.TEXT, InputHint.USERNAME,
         "Username typed into the 'Login' field"),
        (ActionType.FILL, "From", InputKind.DATE, None, "Date typed into the 'From' field"),
        (ActionType.FILL, None, InputKind.TEXT, None, "Value typed into an unnamed field"),
        (ActionType.SELECT, "Country", InputKind.TEXT, None, "Value chosen in the 'Country' list"),
        (ActionType.SELECT, None, InputKind.TEXT, None, "Value chosen in an unnamed list"),
    ],
)  # fmt: skip
def test_field_defaults_name_the_field(
    action: ActionType, label: str | None, kind: InputKind, hint: InputHint | None, expected: str
) -> None:
    assert default_input_description(step(action, label), "v", kind, hint) == expected
