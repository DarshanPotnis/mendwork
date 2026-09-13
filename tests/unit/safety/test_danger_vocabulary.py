"""The risk classifier and the healer read danger from one vocabulary, through one function.

If they could disagree, the classifier might call a control harmless while the healer refused
it, or worse, the healer might accept a control the classifier calls destructive.
"""

import ast
from pathlib import Path
from typing import Final

from hypothesis import given
from hypothesis import strategies as st

from mendwork.apps.cli.wiring import healing_config, risk_vocabulary
from mendwork.engine.domain.enums import ActionType, RiskLevel
from mendwork.engine.healing.checks import safety_rejection
from mendwork.engine.safety import heal_policy, risk
from mendwork.engine.safety.heal_policy import introduced_danger
from mendwork.engine.safety.risk import RiskSignals, classify_risk, danger_words_in
from mendwork.settings import (
    DEFAULT_DANGER_WORDS,
    DEFAULT_READ_WORDS,
    DEFAULT_SESSION_PHRASES,
    DEFAULT_SOFT_VERBS,
    Settings,
)
from tests.unit.healing.builders import export_button, live, step
from tests.unit.recording.builders import VOCABULARY
from tests.workflows import click_step

ENGINE: Final = Path(__file__).resolve().parents[3] / "src" / "mendwork" / "engine"
HEALER_FILES: Final = (
    *sorted((ENGINE / "healing").glob("*.py")),
    ENGINE / "safety" / "heal_policy.py",
    ENGINE / "safety" / "heal_kinds.py",
    ENGINE / "replay" / "heal_record.py",
    ENGINE / "replay" / "step_healing.py",
)
VOCABULARY_WORDS: Final = (
    DEFAULT_DANGER_WORDS | DEFAULT_SOFT_VERBS | DEFAULT_READ_WORDS | DEFAULT_SESSION_PHRASES
)
NEUTRAL: Final = ("ledger", "quarterly", "summary", "invoice", "filter", "all", "now")


def _word_collections(path: Path) -> list[set[str]]:
    collections: list[set[str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Set | ast.List | ast.Tuple):
            words = {
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            collections.append(words)
    return collections


def test_the_healer_holds_no_word_list_of_its_own() -> None:
    assert len(HEALER_FILES) > 10
    offenders = {
        str(path.relative_to(ENGINE)): sorted(words & VOCABULARY_WORDS)
        for path in HEALER_FILES
        for words in _word_collections(path)
        if len(words & VOCABULARY_WORDS) >= 2
    }

    assert offenders == {}


def test_the_healer_calls_the_classifiers_own_functions() -> None:
    bindings = vars(heal_policy)

    assert bindings["danger_words_in"] is risk.danger_words_in
    assert bindings["authentication_reason"] is risk.authentication_reason


@given(
    st.lists(
        st.sampled_from(sorted(DEFAULT_DANGER_WORDS | DEFAULT_SOFT_VERBS) + list(NEUTRAL)),
        min_size=1,
        max_size=4,
    ).map(" ".join)
)
def test_the_classifier_and_the_healer_agree_on_every_name(name: str) -> None:
    irreversible = classify_risk(RiskSignals(action=ActionType.CLICK, name=name), VOCABULARY)

    assert (irreversible.level is RiskLevel.IRREVERSIBLE) == bool(danger_words_in(name, VOCABULARY))
    assert introduced_danger(["Quarterly summary"], [name], VOCABULARY) == danger_words_in(
        name, VOCABULARY
    )


def test_one_setting_changes_what_both_consider_dangerous() -> None:
    settings = Settings(_env_file=None, risk_danger_words=DEFAULT_DANGER_WORDS | {"sunset"})
    vocabulary = risk_vocabulary(settings)
    recorded = export_button()
    candidate = live(recorded, identity={"name": "Sunset ledger"})

    rejection = safety_rejection(
        step(click_step(target=recorded.model_dump())),
        recorded,
        candidate,
        healing_config(settings).vocabulary,
    )

    assert healing_config(settings).vocabulary == vocabulary
    classified = classify_risk(
        RiskSignals(action=ActionType.CLICK, name="Sunset ledger"), vocabulary
    )
    assert classified.level is RiskLevel.IRREVERSIBLE
    assert rejection is not None
    assert '"sunset"' in rejection.detail
