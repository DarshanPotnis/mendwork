"""Risk classification by consequence: a table of controls, including the tricky ones."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from mendwork.engine.domain.enums import ActionType, RiskLevel
from mendwork.engine.safety.risk import RiskSignals, RiskVocabulary, classify_risk
from tests.unit.recording.builders import VOCABULARY

ORDER = {RiskLevel.SAFE: 0, RiskLevel.CAUTION: 1, RiskLevel.IRREVERSIBLE: 2}
CLICK = ActionType.CLICK

TABLE = [
    # Navigating reads; form edits are reversible.
    ("navigate", ActionType.NAVIGATE, None, None, {}, RiskLevel.SAFE),
    ("fill", ActionType.FILL, "textbox", "Email address", {}, RiskLevel.CAUTION),
    ("select", ActionType.SELECT, "combobox", "Delete mode", {}, RiskLevel.CAUTION),
    # The tricky cases.
    (
        "Submit feedback",
        CLICK,
        "button",
        "Submit feedback",
        {"form_submit": True},
        RiskLevel.IRREVERSIBLE,
    ),
    ("Remove filter", CLICK, "button", "Remove filter", {}, RiskLevel.SAFE),
    ("Delete filter", CLICK, "button", "Delete filter", {}, RiskLevel.IRREVERSIBLE),
    ("Remove alone", CLICK, "button", "Remove", {}, RiskLevel.IRREVERSIBLE),
    ("Apply filter submit", CLICK, "button", "Apply filter", {"form_submit": True}, RiskLevel.SAFE),
    (
        "Sign in",
        CLICK,
        "button",
        "Sign in",
        {"form_submit": True, "form_has_password": True},
        RiskLevel.CAUTION,
    ),
    ("Sign out", CLICK, "button", "Sign out", {}, RiskLevel.CAUTION),
    ("Delete account", CLICK, "button", "Delete account", {}, RiskLevel.IRREVERSIBLE),
    ("Save settings", CLICK, "button", "Save settings", {}, RiskLevel.IRREVERSIBLE),
    ("Cancel all orders", CLICK, "button", "Cancel all orders", {}, RiskLevel.IRREVERSIBLE),
    (
        "danger wins over a link",
        CLICK,
        "link",
        "Delete data",
        {"link": True},
        RiskLevel.IRREVERSIBLE,
    ),
    ("danger wins over signing in", CLICK, "button", "Sign in and pay", {}, RiskLevel.IRREVERSIBLE),
    ("payment is not pay", CLICK, "link", "Payment history", {"link": True}, RiskLevel.SAFE),
    # Unknown is CAUTION, never IRREVERSIBLE.
    ("Continue submit", CLICK, "button", "Continue", {"form_submit": True}, RiskLevel.CAUTION),
    ("Continue button", CLICK, "button", "Continue", {}, RiskLevel.CAUTION),
    ("Next", CLICK, "button", "Next", {}, RiskLevel.CAUTION),
    ("OK", CLICK, "button", "OK", {}, RiskLevel.CAUTION),
    ("unnamed", CLICK, "button", None, {}, RiskLevel.CAUTION),
    # Reads.
    ("Download CSV", CLICK, "button", "Download CSV", {"downloaded": True}, RiskLevel.SAFE),
    (
        "a download with no word",
        CLICK,
        "button",
        "Report.csv",
        {"downloaded": True},
        RiskLevel.SAFE,
    ),
    ("View reports", CLICK, "link", "View reports", {"link": True}, RiskLevel.SAFE),
    ("a plain link", CLICK, "link", "Harborline Supply", {"link": True}, RiskLevel.SAFE),
    ("a tab", CLICK, "tab", "Settings", {}, RiskLevel.SAFE),
    ("a checkbox", CLICK, "checkbox", "Remember me", {}, RiskLevel.CAUTION),
    (
        "Enter in a sign-in form",
        ActionType.PRESS,
        "textbox",
        "Password",
        {"form_submit": True, "form_has_password": True},
        RiskLevel.CAUTION,
    ),
    ("Escape", ActionType.PRESS, None, None, {}, RiskLevel.CAUTION),
]


@pytest.mark.parametrize(
    ("case", "action", "role", "name", "extra", "expected"),
    TABLE,
    ids=[row[0] for row in TABLE],
)
def test_risk_is_classified_by_consequence(
    case: str,
    action: ActionType,
    role: str | None,
    name: str | None,
    extra: dict[str, bool],
    expected: RiskLevel,
) -> None:
    signals = RiskSignals(action=action, role=role, name=name, **extra)

    assessment = classify_risk(signals, VOCABULARY)

    assert assessment.level is expected, (case, assessment.reasons)
    assert assessment.reasons


def test_reasons_name_the_words_that_decided() -> None:
    assessment = classify_risk(
        RiskSignals(action=CLICK, role="button", name="Delete account"), VOCABULARY
    )

    assert assessment.reasons == ('its name contains "delete"',)


def test_a_view_state_noun_must_also_be_a_read_word() -> None:
    with pytest.raises(ValidationError, match="missing: rows"):
        RiskVocabulary(
            danger_words=frozenset({"remove"}),
            soft_verbs=frozenset({"remove"}),
            view_state_nouns=frozenset({"rows"}),
            read_words=frozenset(),
            session_phrases=frozenset(),
        )


WORDS = sorted(
    VOCABULARY.read_words
    | VOCABULARY.soft_verbs
    | VOCABULARY.view_state_nouns
    | {"sign", "in", "out", "continue", "report", "account", "ok"}
)


@given(
    words=st.lists(st.sampled_from(WORDS), max_size=5),
    danger=st.sampled_from(sorted(VOCABULARY.danger_words)),
    position=st.integers(min_value=0, max_value=5),
    form_submit=st.booleans(),
    form_has_password=st.booleans(),
    link=st.booleans(),
    downloaded=st.booleans(),
    role=st.sampled_from([None, "button", "link", "checkbox", "tab"]),
)
def test_adding_a_danger_word_never_lowers_the_level(
    words: list[str],
    danger: str,
    position: int,
    form_submit: bool,
    form_has_password: bool,
    link: bool,
    downloaded: bool,
    role: str | None,
) -> None:
    flags = {
        "form_submit": form_submit,
        "form_has_password": form_has_password,
        "link": link,
        "downloaded": downloaded,
    }
    with_danger = [*words]
    with_danger.insert(min(position, len(words)), danger)

    before = classify_risk(
        RiskSignals(action=CLICK, role=role, name=" ".join(words), **flags), VOCABULARY
    )
    after = classify_risk(
        RiskSignals(action=CLICK, role=role, name=" ".join(with_danger), **flags), VOCABULARY
    )

    assert ORDER[after.level] >= ORDER[before.level]
