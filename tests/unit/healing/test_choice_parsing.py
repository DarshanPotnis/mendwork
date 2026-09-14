"""A model's reply is read strictly: one JSON object with a choice, a confidence, and a reason."""

import json
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.healing.choice import ChoiceProblem, ParsedChoice, on_the_list, parse_choice
from mendwork.engine.healing.prompt import REASON_MAX_CHARS

VALID: Final = [
    ('{"choice": 2, "confidence": 0.8, "reason": "Same control."}', (2, 0.8, "Same control.")),
    ('  \n{"choice": null, "confidence": 0, "reason": "None fit."}\n', (None, 0.0, "None fit.")),
    ('{"choice": 1, "confidence": 1, "reason": "x"}', (1, 1.0, "x")),
    ('{"reason": "Order is free.", "confidence": 0.5, "choice": 3}', (3, 0.5, "Order is free.")),
    (
        '{"choice": 0, "confidence": 0.5, "reason": "Parsed; range is judged later."}',
        (0, 0.5, None),
    ),
    (
        '{"choice": 99, "confidence": 0.5, "reason": "Parsed; range is judged later."}',
        (99, 0.5, None),
    ),
    (
        json.dumps({"choice": 1, "confidence": 0.5, "reason": "r" * REASON_MAX_CHARS}),
        (1, 0.5, None),
    ),
]
PROBLEMS: Final = [
    ("", ChoiceProblem.EMPTY),
    ("   \n", ChoiceProblem.EMPTY),
    ("2", ChoiceProblem.NOT_ONE_OBJECT),
    ("null", ChoiceProblem.NOT_ONE_OBJECT),
    ('```json\n{"choice": 1, "confidence": 0.5, "reason": "r"}\n```', ChoiceProblem.NOT_ONE_OBJECT),
    ('The answer is {"choice": 1, "confidence": 0.5, "reason": "r"}', ChoiceProblem.NOT_ONE_OBJECT),
    (
        '{"choice": 1, "confidence": 0.5, "reason": "r"} {"choice": 2, "confidence": 0.5, '
        '"reason": "r"}',
        ChoiceProblem.NOT_ONE_OBJECT,
    ),
    ('[{"choice": 1, "confidence": 0.5, "reason": "r"}]', ChoiceProblem.NOT_ONE_OBJECT),
    ('{"choice": 1, "choice": 2, "confidence": 0.5, "reason": "r"}', ChoiceProblem.NOT_ONE_OBJECT),
    ('{"choice": 1, "confidence": NaN, "reason": "r"}', ChoiceProblem.NOT_ONE_OBJECT),
    ('{"choice": 1, "confidence": Infinity, "reason": "r"}', ChoiceProblem.NOT_ONE_OBJECT),
    ('{"choice": 1, "confidence": 0.5, "reason": "r"', ChoiceProblem.NOT_ONE_OBJECT),
    ('{"choice": 1, "confidence": 0.5}', ChoiceProblem.FIELDS),
    (
        '{"choice": 1, "confidence": 0.5, "reason": "r", "selector": "#export"}',
        ChoiceProblem.FIELDS,
    ),
    ('{"choice": "2", "confidence": 0.5, "reason": "r"}', ChoiceProblem.CHOICE),
    ('{"choice": 2.0, "confidence": 0.5, "reason": "r"}', ChoiceProblem.CHOICE),
    ('{"choice": true, "confidence": 0.5, "reason": "r"}', ChoiceProblem.CHOICE),
    ('{"choice": [1], "confidence": 0.5, "reason": "r"}', ChoiceProblem.CHOICE),
    ('{"choice": 1, "confidence": "0.9", "reason": "r"}', ChoiceProblem.CONFIDENCE),
    ('{"choice": 1, "confidence": 1.5, "reason": "r"}', ChoiceProblem.CONFIDENCE),
    ('{"choice": 1, "confidence": -0.1, "reason": "r"}', ChoiceProblem.CONFIDENCE),
    ('{"choice": 1, "confidence": 85, "reason": "r"}', ChoiceProblem.CONFIDENCE),
    ('{"choice": 1, "confidence": false, "reason": "r"}', ChoiceProblem.CONFIDENCE),
    ('{"choice": 1, "confidence": 0.5, "reason": ""}', ChoiceProblem.REASON),
    ('{"choice": 1, "confidence": 0.5, "reason": "   "}', ChoiceProblem.REASON),
    ('{"choice": 1, "confidence": 0.5, "reason": null}', ChoiceProblem.REASON),
    ('{"choice": 1, "confidence": 0.5, "reason": 5}', ChoiceProblem.REASON),
    (
        json.dumps({"choice": 1, "confidence": 0.5, "reason": "r" * (REASON_MAX_CHARS + 1)}),
        ChoiceProblem.REASON,
    ),
]


@pytest.mark.parametrize(("text", "expected"), VALID)
def test_a_reply_in_the_required_shape_is_read_exactly(
    text: str, expected: tuple[int | None, float, str | None]
) -> None:
    parsed = parse_choice(text)

    assert isinstance(parsed, ParsedChoice)
    choice, confidence, reason = expected
    assert (parsed.choice, parsed.confidence) == (choice, confidence)
    if reason is not None:
        assert parsed.reason == reason


@pytest.mark.parametrize(("text", "problem"), PROBLEMS)
def test_every_other_shape_is_a_named_problem_never_a_guess(
    text: str, problem: ChoiceProblem
) -> None:
    assert parse_choice(text) is problem


def test_every_problem_is_described_in_words_a_repair_request_can_show() -> None:
    assert all(problem.value and not problem.value.endswith(".") for problem in ChoiceProblem)


def test_only_a_listed_number_is_on_the_list() -> None:
    assert [on_the_list(choice, 3) for choice in (-1, 0, 1, 3, 4)] == [
        False,
        False,
        True,
        True,
        False,
    ]


@given(st.text())
def test_parsing_never_raises_whatever_the_model_sends(text: str) -> None:
    assert isinstance(parse_choice(text), ParsedChoice | ChoiceProblem)


@given(
    choice=st.none() | st.integers(min_value=-5, max_value=50),
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    reason=st.text(min_size=1, max_size=REASON_MAX_CHARS).filter(lambda text: text.strip() != ""),
)
def test_every_well_formed_reply_round_trips(
    choice: int | None, confidence: float, reason: str
) -> None:
    text = json.dumps({"choice": choice, "confidence": confidence, "reason": reason})

    parsed = parse_choice(text)

    assert parsed == ParsedChoice(choice=choice, confidence=confidence, reason=reason)
