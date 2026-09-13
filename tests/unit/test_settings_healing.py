"""Healing settings: defaults, the safety invariants Settings refuses to break, and wiring."""

import pytest
from pydantic import ValidationError

from mendwork.apps.cli.wiring import healing_config, replay_config, risk_vocabulary
from mendwork.engine.domain.heals import FeatureName
from mendwork.engine.healing.config import FeatureWeights, HealingConfig, acceptance_problems
from mendwork.settings import Settings
from tests.unit.replay.builders import WEIGHTS, healing


def test_the_defaults_are_the_derived_acceptance_rule() -> None:
    settings = Settings(_env_file=None)

    assert sum(settings.heal_weights().values()) == pytest.approx(1.0)
    assert (settings.heal_accept_threshold, settings.heal_accept_margin) == (0.60, 0.15)
    assert settings.heal_name_similarity_floor == 0.5
    assert settings.heal_candidates_max == 4_000
    assert (settings.heal_max_attempts, settings.heal_authentication_max_attempts) == (2, 1)
    assert settings.heal_timeout_ms == 30_000


def test_context_weights_that_could_reach_the_threshold_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENDWORK_HEAL_WEIGHT_NAME", "0.05")
    monkeypatch.setenv("MENDWORK_HEAL_WEIGHT_POSITION", "0.30")

    with pytest.raises(ValidationError, match="not below the accept threshold"):
        Settings(_env_file=None)


def test_a_margin_one_weak_clue_could_open_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_HEAL_ACCEPT_MARGIN", "0.10")

    with pytest.raises(ValidationError, match="must be larger than every nearby-text"):
        Settings(_env_file=None)


def test_weights_that_do_not_sum_to_one_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_HEAL_WEIGHT_NAME", "0.35")

    with pytest.raises(ValidationError, match="must sum to 1"):
        Settings(_env_file=None)


def test_a_safe_change_to_the_rule_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_HEAL_ACCEPT_THRESHOLD", "0.70")
    monkeypatch.setenv("MENDWORK_HEAL_ACCEPT_MARGIN", "0.20")

    config = healing_config(Settings(_env_file=None))

    assert (config.accept_threshold, config.accept_margin) == (0.70, 0.20)


@pytest.mark.parametrize("value", ["2", "-1"])
def test_authentication_steps_get_at_most_one_attempt(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MENDWORK_HEAL_AUTHENTICATION_MAX_ATTEMPTS", value)

    with pytest.raises(ValidationError, match="heal_authentication_max_attempts"):
        Settings(_env_file=None)


def test_the_engine_configuration_is_built_from_settings_with_the_risk_vocabulary() -> None:
    settings = Settings(_env_file=None)

    config = healing_config(settings)

    assert config == healing()
    assert config.weights == WEIGHTS
    assert config.vocabulary == risk_vocabulary(settings)
    assert replay_config(settings).healing == config


def test_the_engine_refuses_an_unsafe_configuration_on_its_own() -> None:
    unsafe = WEIGHTS.model_copy(update={"name": 0.05, "position": 0.30})

    with pytest.raises(ValidationError, match="not below the accept threshold"):
        healing(weights=unsafe)
    with pytest.raises(ValidationError):
        HealingConfig.model_validate({**healing().model_dump(), "accept_margin": 0.10})


def test_missing_weights_are_named() -> None:
    weights = {FeatureName.NAME: 1.0}

    assert acceptance_problems(weights, 0.6, 0.15) == (
        "heal feature weights are missing: attributes, label, nearby_text, position, role, "
        "structural_path, tag_type",
    )


def test_weights_by_feature_cover_every_feature() -> None:
    assert set(FeatureWeights.model_validate(WEIGHTS.model_dump()).by_feature()) == set(FeatureName)
