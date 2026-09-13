"""Rung 2's features, one clue at a time, including the stand-ins for clues that cannot exist."""

import pytest

from mendwork.engine.domain.fingerprint import NormalizedBox
from mendwork.engine.healing.features import (
    attribute_overlap,
    feature_scores,
    label_similarity,
    name_similarity,
    nearby_text_similarity,
    position_proximity,
    role_match,
    structural_path_similarity,
    tag_type_match,
    text_similarity,
)
from mendwork.engine.ports.element_types import Box
from tests.unit.healing.builders import export_button, live, reference_field
from tests.unit.replay.builders import healing

FLOOR = 0.5


def test_texts_equal_after_normalization_are_the_same() -> None:
    assert text_similarity("Export  ledger", "export ledger", FLOOR) == 1.0


def test_missing_text_has_nothing_in_common() -> None:
    assert text_similarity(None, "Export ledger", FLOOR) == 0.0
    assert text_similarity("Export ledger", "", FLOOR) == 0.0


def test_agreement_at_or_below_the_floor_counts_as_nothing() -> None:
    assert text_similarity("Export ledger", "Invite teammate", FLOOR) == 0.0


def test_partial_agreement_above_the_floor_scales_between_zero_and_one() -> None:
    similar = text_similarity("Export ledger", "Export ledgers", FLOOR)

    assert 0.0 < similar < 1.0
    assert text_similarity("Export ledger", "Export ledgers", 0.0) > similar


def test_an_element_without_a_name_is_compared_by_its_visible_text() -> None:
    recorded = export_button(accessible_name=None)
    candidate = live(recorded, identity={"name": ""}, facts={"text": "Export ledger"})

    assert name_similarity(recorded, candidate, FLOOR) == 1.0


def test_a_control_without_a_label_scores_its_label_feature_by_name() -> None:
    recorded = export_button()
    candidate = live(recorded, identity={"name": "Export ledgers"})

    assert label_similarity(recorded, candidate, FLOOR) == name_similarity(
        recorded, candidate, FLOOR
    )


def test_a_labelled_field_compares_labels() -> None:
    recorded = reference_field()
    renamed = live(recorded, facts={"label_text": "Client code"})

    assert label_similarity(recorded, live(recorded), FLOOR) == 1.0
    assert label_similarity(recorded, renamed, FLOOR) == 0.0


def test_attribute_overlap_is_the_share_of_recorded_attributes_that_survived() -> None:
    recorded = export_button()

    assert attribute_overlap(recorded, live(recorded)) == 1.0
    assert attribute_overlap(recorded, live(recorded, facts={"id": "export-ledger-2"})) == 0.5
    assert (
        attribute_overlap(recorded, live(recorded, facts={"id": None, "data_testid": None})) == 0.0
    )


def test_a_fingerprint_without_identity_attributes_gains_nothing_from_them() -> None:
    recorded = export_button(attributes={"type": "button"})

    assert attribute_overlap(recorded, live(recorded)) == 0.0


def test_text_attributes_compare_after_normalization() -> None:
    recorded = export_button(attributes={"aria_label": "Export  Ledger"})

    assert attribute_overlap(recorded, live(recorded, facts={"aria_label": "export ledger"})) == 1.0


@pytest.mark.parametrize(
    ("role", "expected"), [("button", 1.0), ("link", 0.5), ("menuitem", 0.5), ("checkbox", 0.0)]
)
def test_roles_match_fully_within_the_activation_family_partly(role: str, expected: float) -> None:
    recorded = export_button()

    assert role_match(recorded, live(recorded, identity={"role": role})) == expected


def test_a_fingerprint_without_a_role_compares_tag_and_type_instead() -> None:
    recorded = reference_field(role=None, attributes={"id": "due", "type": "date"})

    assert role_match(recorded, live(recorded, identity={"role": "textbox"})) == 1.0
    assert role_match(recorded, live(recorded, identity={"input_type": "time"})) == 0.5


def test_tag_and_type_apply_html_defaults() -> None:
    recorded = export_button(attributes={"id": "export-ledger"})  # a button without a type submits

    assert tag_type_match(recorded, live(recorded, identity={"input_type": "submit"})) == 1.0
    assert tag_type_match(recorded, live(recorded, identity={"input_type": "button"})) == 0.5
    assert tag_type_match(recorded, live(recorded, identity={"tag": "a"})) == 0.0


def test_structural_paths_compare_level_by_level() -> None:
    assert structural_path_similarity("main > div > button", "main > div > button") == 1.0
    assert structural_path_similarity("main > div > button", "main > div > a") == pytest.approx(
        2 / 3
    )
    assert structural_path_similarity("main > div > button", "footer > p") == 0.0


def test_nearby_text_averages_each_recorded_texts_best_match() -> None:
    recorded = export_button(nearby_text=["Quarterly ledger", "Finance"])

    both = live(recorded, facts={"nearby_text": ("Finance", "Quarterly ledger")})
    one = live(recorded, facts={"nearby_text": ("Quarterly ledger",)})
    none = live(recorded, facts={"nearby_text": ()})

    assert nearby_text_similarity(recorded, both, FLOOR) == 1.0
    assert nearby_text_similarity(recorded, one, FLOOR) == 0.5
    assert nearby_text_similarity(recorded, none, FLOOR) == 0.0


def test_a_fingerprint_without_nearby_text_uses_its_structural_path() -> None:
    recorded = export_button(nearby_text=[])
    moved = live(recorded, facts={"structural_path": "main > article > button"})

    assert nearby_text_similarity(recorded, moved, FLOOR) == structural_path_similarity(
        recorded.structural_path, "main > article > button"
    )


def test_position_proximity_falls_from_one_to_zero_over_the_scale() -> None:
    recorded = NormalizedBox(x=0.5, y=0.5, width=0.1, height=0.1)

    assert position_proximity(recorded, Box(x=0.5, y=0.5, width=0.1, height=0.1), 0.25) == 1.0
    assert position_proximity(recorded, Box(x=0.625, y=0.5, width=0.1, height=0.1), 0.25) == (
        pytest.approx(0.5)
    )
    assert position_proximity(recorded, Box(x=0.0, y=0.0, width=0.1, height=0.1), 0.25) == 0.0
    assert position_proximity(None, Box(x=0.5, y=0.5, width=0.1, height=0.1), 0.25) == 0.0
    assert position_proximity(recorded, None, 0.25) == 0.0


def test_an_unchanged_element_matches_on_every_feature() -> None:
    recorded = reference_field()

    scores = feature_scores(recorded, live(recorded), healing())

    assert set(scores.model_dump().values()) == {1.0}
