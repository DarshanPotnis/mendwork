"""Risk classification by consequence: what an action changes, not how it is performed.

Keywords and form submission are signals. The order they are read in is the policy:

1. navigating reads only (SAFE); filling and choosing change unsaved form state (CAUTION);
2. a danger word in the control's name means stored data changes or others are affected
   (IRREVERSIBLE), whatever kind of element it is. A soft verb acting on view state, as in
   "Remove filter", is not a danger word in that name;
3. signing in or out, or submitting a form that holds a password, changes the session
   reversibly (CAUTION);
4. reading words, a download, or a plain link read or navigate only (SAFE);
5. toggling a form control changes unsaved form state (CAUTION);
6. anything else is unknown, and unknown is CAUTION. "Continue", "Next", and "OK" are
   everywhere; calling them irreversible would make approvals so frequent that people
   stop reading them, which is less safe, not more.

A danger word never lowers a level, and the vocabulary comes from Settings.
"""

from collections.abc import Iterable
from typing import Self

from pydantic import model_validator

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.credentials import tokenize
from mendwork.engine.domain.enums import ActionType, RiskLevel

_FORM_CONTROL_ROLES = frozenset(
    {
        "checkbox",
        "combobox",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "radio",
        "slider",
        "spinbutton",
        "switch",
    }
)
_READ_ROLES = frozenset({"tab"})


class RiskVocabulary(DomainModel):
    """The words risk classification reads in a control's name, one token or phrase each."""

    danger_words: frozenset[str]
    soft_verbs: frozenset[str]
    """Danger words that change only view state when their object is a view-state noun."""
    view_state_nouns: frozenset[str]
    read_words: frozenset[str]
    session_phrases: frozenset[str]

    @model_validator(mode="after")
    def _view_state_is_read_only(self) -> Self:
        # "Remove filter" drops "remove" and must then read as a filter, which is a read.
        missing = self.view_state_nouns - self.read_words
        if missing:
            raise ValueError(
                "every view-state noun must also be a read word; missing: "
                + ", ".join(sorted(missing))
            )
        return self


class RiskSignals(DomainModel):
    """What the recorder observed about a step."""

    action: ActionType
    role: str | None = None
    name: str | None = None
    form_submit: bool = False
    """The action submits a form (a submit button, or Enter in a form's field)."""
    form_has_password: bool = False
    link: bool = False
    """The target is a link with an href."""
    downloaded: bool = False


class RiskAssessment(DomainModel):
    """A risk level and the signals that decided it."""

    level: RiskLevel
    reasons: tuple[str, ...]


def classify_risk(signals: RiskSignals, vocabulary: RiskVocabulary) -> RiskAssessment:
    """The risk of a recorded step, with reasons a reviewer can check."""
    if signals.action is ActionType.NAVIGATE:
        return _assess(RiskLevel.SAFE, "navigates only")
    if signals.action in {ActionType.FILL, ActionType.SELECT}:
        return _assess(RiskLevel.CAUTION, "changes unsaved form state")

    tokens = tokenize(signals.name or "")
    danger = _danger_words(tokens, vocabulary)
    if danger:
        return _assess(RiskLevel.IRREVERSIBLE, f"its name contains {_quoted(danger)}")
    session = _phrases(tokens, vocabulary.session_phrases)
    if session:
        return _assess(RiskLevel.CAUTION, f"changes the session: {_quoted(session)}")
    if signals.form_submit and signals.form_has_password:
        return _assess(RiskLevel.CAUTION, "submits a form holding a password (a sign-in)")
    read = sorted(set(tokens) & vocabulary.read_words)
    if read:
        return _assess(RiskLevel.SAFE, f"reads only: {_quoted(read)}")
    if signals.downloaded:
        return _assess(RiskLevel.SAFE, "downloads a file")
    if signals.link and not signals.form_submit:
        return _assess(RiskLevel.SAFE, "follows a link")
    if signals.role in _READ_ROLES:
        return _assess(RiskLevel.SAFE, f"switches a {signals.role}")
    if signals.role in _FORM_CONTROL_ROLES:
        return _assess(RiskLevel.CAUTION, "changes unsaved form state")
    if signals.form_submit:
        return _assess(RiskLevel.CAUTION, "submits a form, and no word says what it changes")
    return _assess(
        RiskLevel.CAUTION, "no word says what it changes, so it is treated as reversible"
    )


def _danger_words(tokens: tuple[str, ...], vocabulary: RiskVocabulary) -> list[str]:
    found = sorted(set(tokens) & vocabulary.danger_words)
    if not found:
        return []
    softened = set(found) <= vocabulary.soft_verbs and bool(
        set(tokens) & vocabulary.view_state_nouns
    )
    return [] if softened else found


def _phrases(tokens: tuple[str, ...], phrases: Iterable[str]) -> list[str]:
    found: list[str] = []
    for phrase in sorted(phrases):
        words = tokenize(phrase)
        width = len(words)
        if width and any(
            tokens[start : start + width] == words for start in range(len(tokens) - width + 1)
        ):
            found.append(phrase)
    return found


def _quoted(words: Iterable[str]) -> str:
    return ", ".join(f'"{word}"' for word in words)


def _assess(level: RiskLevel, reason: str) -> RiskAssessment:
    return RiskAssessment(level=level, reasons=(reason,))
