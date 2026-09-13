"""Where synthetic archetypes land against the default threshold and margin (ADR 0009).

Computed by the real scoring function on a made-up ledger export button, so the numbers the
acceptance rule was derived from stay true. Each archetype changes one kind of clue a release
changes: the wording, the identity attributes, the element kind, the ancestry, the position.
"""

from typing import Final

import pytest

from mendwork.engine.domain.heals import CandidateOrigin, RungOutcome
from mendwork.engine.healing.acceptance import Accepted, Declined, decide
from mendwork.engine.healing.scoring import rank, score_candidate
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.ports.element_types import Box
from tests.unit.healing.builders import export_button, live
from tests.unit.replay.builders import healing

CONFIG = healing()
RECORDED = export_button()
FAR: Final = Box(x=0.0, y=0.9, width=0.12, height=0.05)
RENAMED: Final = {"name": "Download statements"}
RENAMED_TEXT: Final = {"text": "Download statements"}
NEW_ATTRIBUTES: Final = {"id": "el-81fa2c", "data_testid": "tid-9xq2"}


def score(candidate: LiveCandidate) -> float:
    return score_candidate(RECORDED, candidate, CandidateOrigin.PAGE, CONFIG).score


ARCHETYPES: Final = [
    ("unchanged", live(RECORDED), 1.00),
    ("identity attributes regenerated", live(RECORDED, facts=NEW_ATTRIBUTES), 0.75),
    ("wording changed", live(RECORDED, identity=RENAMED, facts=RENAMED_TEXT), 0.70),
    (
        "wording changed and moved anywhere",
        live(RECORDED, identity=RENAMED, facts={**RENAMED_TEXT, "box": FAR}),
        0.60,
    ),
    (
        "button became a link",
        live(
            RECORDED,
            identity={"tag": "a", "role": "link", "input_type": None},
            facts={"href": "/ledger", "structural_path": "main > article > div > a"},
        ),
        0.875,
    ),
    (
        "same name elsewhere, nothing else in common",
        live(
            RECORDED,
            facts={
                "id": None,
                "data_testid": None,
                "nearby_text": ("Payroll",),
                "structural_path": "footer > div > button",
                "box": FAR,
            },
        ),
        0.507142857,
    ),
    (
        "wording and identity attributes both changed",
        live(RECORDED, identity=RENAMED, facts={**RENAMED_TEXT, **NEW_ATTRIBUTES}),
        0.45,
    ),
]


@pytest.mark.parametrize(("archetype", "candidate", "expected"), ARCHETYPES)
def test_each_archetype_scores_what_the_invariants_predict(
    archetype: str, candidate: LiveCandidate, expected: float
) -> None:
    assert score(candidate) == pytest.approx(expected, abs=1e-6), archetype


def test_losing_any_one_group_of_clues_still_reaches_the_threshold() -> None:
    accepted = [name for name, candidate, _ in ARCHETYPES[:5] if score(candidate) >= 0.60]

    assert accepted == [name for name, _, _ in ARCHETYPES[:5]]


def test_losing_both_identity_anchors_never_reaches_it() -> None:
    assert all(score(candidate) < CONFIG.accept_threshold for _, candidate, _ in ARCHETYPES[5:])


def test_two_copies_that_differ_only_in_position_are_too_close_to_choose_between() -> None:
    copy = live(RECORDED, "copy", facts={"box": Box(x=0.72, y=0.3, width=0.12, height=0.05)})
    items = [
        score_candidate(RECORDED, candidate, CandidateOrigin.PAGE, CONFIG)
        for candidate in (live(RECORDED), copy)
    ]

    decision = decide(
        rank(items), threshold=CONFIG.accept_threshold, required_margin=CONFIG.accept_margin
    )

    assert isinstance(decision, Declined)
    assert decision.outcome is RungOutcome.BELOW_MARGIN
    assert decision.margin == pytest.approx(0.048)


def test_a_renamed_control_is_told_apart_from_an_unrelated_neighbour() -> None:
    renamed = live(RECORDED, "renamed", identity=RENAMED, facts=RENAMED_TEXT)
    neighbour = live(
        RECORDED,
        "neighbour",
        identity={"name": "Invite teammate"},
        facts={"text": "Invite teammate", "id": None, "data_testid": None},
    )
    items = [
        score_candidate(RECORDED, candidate, CandidateOrigin.PAGE, CONFIG)
        for candidate in (renamed, neighbour)
    ]

    decision = decide(
        rank(items), threshold=CONFIG.accept_threshold, required_margin=CONFIG.accept_margin
    )

    assert isinstance(decision, Accepted)
    assert decision.winner.candidate.element == renamed.element
    assert decision.margin == pytest.approx(0.25)
