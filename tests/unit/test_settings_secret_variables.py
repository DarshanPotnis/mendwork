"""The MENDWORK_SECRET_ namespace is reserved without weakening typo detection (ADR 0003)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from mendwork.adapters.secrets_env.naming import (
    is_valid_secret_variable,
    secret_variable_name,
)
from mendwork.apps.cli.wiring import replay_config
from mendwork.engine.domain.identifiers import SecretName
from mendwork.settings import Settings


def test_a_well_formed_secret_variable_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_SECRET_PORTAL_PASSWORD", "harbor-demo")

    Settings(_env_file=None)


@pytest.mark.parametrize(
    "variable",
    [
        "MENDWORK_SECRET_bad-name",
        "MENDWORK_SECRET_portal_password",
        "MENDWORK_SECRET_",
        "MENDWORK_SECRET_A__B",
        "MENDWORK_SECRET_1ABC",
    ],
)
def test_a_malformed_secret_variable_is_rejected_with_the_naming_rule(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "leaked-7731")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    message = str(caught.value)
    assert variable in message
    assert "UPPER_SNAKE_CASE" in message
    assert "leaked-7731" not in message


def test_typos_are_still_rejected_next_to_valid_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_SECRET_PORTAL_PASSWORD", "harbor-demo")
    monkeypatch.setenv("MENDWORK_LOG_LEVLE", "DEBUG")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    assert "did you mean MENDWORK_LOG_LEVEL?" in str(caught.value)
    assert "MENDWORK_SECRET_PORTAL_PASSWORD" not in str(caught.value)


def test_a_misspelled_namespace_is_an_ordinary_typo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_SECRETS_PORTAL_PASSWORD", "harbor-demo")

    with pytest.raises(ValidationError, match="MENDWORK_SECRETS_PORTAL_PASSWORD"):
        Settings(_env_file=None)


def test_secrets_in_a_dotenv_file_are_refused(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MENDWORK_SECRET_PORTAL_PASSWORD=harbor-demo\n", encoding="utf-8")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=env_file)

    message = str(caught.value)
    assert "secrets are read from the process environment only, never from .env" in message
    assert "harbor-demo" not in message


def test_no_setting_can_ever_collide_with_the_secret_namespace() -> None:
    assert [name for name in Settings.model_fields if name.startswith("secret_")] == []


def test_rejected_values_are_not_repeated_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_STEP_TIMEOUT_MS", "not-a-number-7731")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    assert "step_timeout_ms" in str(caught.value)
    assert "not-a-number-7731" not in str(caught.value)


def test_retry_delays_must_not_be_inverted() -> None:
    with pytest.raises(ValidationError, match="retry_max_delay_ms"):
        Settings(_env_file=None, retry_initial_delay_ms=5_000, retry_max_delay_ms=1_000)


def test_secret_variables_are_named_as_the_resolver_looks_them_up() -> None:
    assert secret_variable_name(SecretName("portal_password")) == "MENDWORK_SECRET_PORTAL_PASSWORD"
    assert is_valid_secret_variable("MENDWORK_SECRET_PORTAL_PASSWORD")
    assert not is_valid_secret_variable("mendwork_secret_PORTAL_PASSWORD")


def test_replay_timing_defaults_reach_the_engine_unchanged() -> None:
    engine = replay_config(Settings(_env_file=None))

    assert (engine.step_timeout_ms, engine.checkpoint_timeout_ms, engine.navigation_timeout_ms) == (
        10_000,
        10_000,
        30_000,
    )
    assert (engine.run_timeout_ms, engine.settle_timeout_ms, engine.settle_quiet_frames) == (
        600_000,
        2_000,
        2,
    )
    assert engine.retry.model_dump() == {
        "max_attempts": 3,
        "initial_delay_ms": 500,
        "max_delay_ms": 4_000,
        "multiplier": 2.0,
        "jitter_ratio": 0.5,
    }
