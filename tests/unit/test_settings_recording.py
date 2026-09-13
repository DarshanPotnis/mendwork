"""Recording and risk settings: defaults, environment overrides, and a consistent vocabulary."""

import pytest
from pydantic import ValidationError

from mendwork.apps.cli.wiring import (
    record_launch_options,
    recording_config,
    recording_options,
    risk_vocabulary,
)
from mendwork.settings import Settings


def test_recording_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.record_checkpoint_timeout_ms == 1_000
    assert settings.record_scope_ancestors_max == 6
    assert "delete" in settings.risk_danger_words
    assert settings.risk_view_state_nouns <= settings.risk_read_words
    assert "next" not in settings.risk_read_words


def test_the_wiring_builds_the_recorder_from_settings() -> None:
    settings = Settings(_env_file=None)

    config = recording_config(settings)

    assert config.checkpoint_timeout_ms == settings.record_checkpoint_timeout_ms
    assert config.risk == risk_vocabulary(settings)
    assert recording_options(settings).viewport_width == settings.viewport_width
    assert record_launch_options(settings, slow_mo_ms=None).headless is False
    assert record_launch_options(settings, slow_mo_ms=250).slow_mo_ms == 250


def test_vocabulary_can_be_replaced_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_RISK_DANGER_WORDS", '["delete", "wipe"]')
    monkeypatch.setenv("MENDWORK_RISK_SOFT_VERBS", '["delete"]')

    settings = Settings(_env_file=None)

    assert settings.risk_danger_words == frozenset({"delete", "wipe"})


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("MENDWORK_RISK_READ_WORDS", '["View"]', "non-blank and lower case"),
        ("MENDWORK_RISK_SOFT_VERBS", '["hide"]', "soft verb must also be a risk danger word"),
        ("MENDWORK_RISK_READ_WORDS", '["view"]', "view-state noun must also be a risk read word"),
    ],
)
def test_an_inconsistent_vocabulary_is_refused(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, message: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None)
