"""Settings come from the environment under the MENDWORK_ prefix."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import DotEnvSettingsSource

from mendwork.settings import (
    Environment,
    LogLevel,
    Settings,
    _OwnDotEnvSource,
    describe_unknown_variables,
)


def test_defaults_are_development_friendly() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level is LogLevel.INFO
    assert "password" in settings.sensitive_key_fragments


def test_values_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_ENVIRONMENT", "production")
    monkeypatch.setenv("MENDWORK_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("MENDWORK_SENSITIVE_KEY_FRAGMENTS", '["session_id"]')

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.PRODUCTION
    assert settings.log_level is LogLevel.DEBUG
    assert settings.sensitive_key_fragments == frozenset({"session_id"})


def test_an_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_LOG_LEVEL", "chatty")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_an_unknown_setting_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"log_level": "DEBUG", "retries": 3})


def test_a_misspelled_variable_is_rejected_with_a_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENDWORK_LOG_LEVLE", "DEBUG")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    message = str(caught.value)
    assert "MENDWORK_LOG_LEVLE" in message
    assert "did you mean MENDWORK_LOG_LEVEL?" in message


def test_every_misspelled_variable_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_ENVIRONMNT", "production")
    monkeypatch.setenv("MENDWORK_LOG_LEVLE", "DEBUG")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    message = str(caught.value)
    assert "did you mean MENDWORK_ENVIRONMENT?" in message
    assert "did you mean MENDWORK_LOG_LEVEL?" in message


def test_a_variable_with_no_close_match_lists_the_valid_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENDWORK_QUIXOTIC_ZEPHYR", "1")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    message = str(caught.value)
    assert "MENDWORK_QUIXOTIC_ZEPHYR" in message
    assert "valid names are MENDWORK_ARTIFACTS_DIR, " in message
    assert "MENDWORK_LOG_LEVEL" in message


def test_a_dotenv_file_is_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MENDWORK_LOG_LEVEL=DEBUG\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.log_level is LogLevel.DEBUG


def test_a_dotenv_file_shared_with_other_tools_still_loads(tmp_path: Path) -> None:
    # From Phase 10 docker compose reads this same file, so it holds foreign keys.
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=hunter2\nCOMPOSE_PROJECT_NAME=mendwork\nMENDWORK_LOG_LEVEL=DEBUG\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.log_level is LogLevel.DEBUG


def test_a_misspelled_variable_in_a_dotenv_file_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MENDWORK_LOG_LEVLE=DEBUG\n", encoding="utf-8")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=env_file)

    message = str(caught.value)
    assert "MENDWORK_LOG_LEVLE" in message
    assert "did you mean MENDWORK_LOG_LEVEL?" in message


def test_typos_in_both_sources_are_reported_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MENDWORK_LOG_LEVLE=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("MENDWORK_ENVIRONMNT", "production")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=env_file)

    message = str(caught.value)
    assert "did you mean MENDWORK_LOG_LEVEL?" in message
    assert "did you mean MENDWORK_ENVIRONMENT?" in message
    assert message.count("unrecognised environment variables") == 1


def test_the_dotenv_source_delegates_field_lookup(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MENDWORK_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    source = _OwnDotEnvSource(DotEnvSettingsSource(Settings, env_file=env_file))

    value, key, _ = source.get_field_value(Settings.model_fields["log_level"], "log_level")

    assert value == "DEBUG"
    assert key == "log_level"


def test_input_that_is_not_a_mapping_is_left_to_pydantic() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(42)


def test_an_unknown_keyword_argument_is_still_rejected() -> None:
    with pytest.raises(ValidationError):
        # mypy rejects this statically; code callers get no slack from the .env rules.
        Settings(_env_file=None, retries=3)  # type: ignore[call-arg]


def test_the_environment_overrides_the_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MENDWORK_LOG_LEVEL=DEBUG\nMENDWORK_ENVIRONMENT=test\n", encoding="utf-8")
    monkeypatch.setenv("MENDWORK_LOG_LEVEL", "ERROR")

    settings = Settings(_env_file=env_file)

    assert settings.log_level is LogLevel.ERROR
    assert settings.environment is Environment.TEST


def test_unrelated_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORKISH", "not ours")
    monkeypatch.setenv("PYTEST_SOMETHING", "not ours either")
    monkeypatch.setenv("MENDWORK_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.log_level is LogLevel.DEBUG


def test_the_valid_names_come_from_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every field is accepted under its own name, so the check can never drift from
    # the model the way a hardcoded list would.
    for name in Settings.model_fields:
        monkeypatch.setenv(f"MENDWORK_{name.upper()}", "")

    assert describe_unknown_variables(Settings.model_fields, os.environ) is None


def test_the_portal_listens_on_loopback_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.portal_host == "127.0.0.1"
    assert settings.portal_port == 8765


def test_the_portal_address_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_PORTAL_HOST", "0.0.0.0")  # noqa: S104 - value under test, not a bind
    monkeypatch.setenv("MENDWORK_PORTAL_PORT", "0")

    settings = Settings(_env_file=None)

    assert (settings.portal_host, settings.portal_port) == ("0.0.0.0", 0)  # noqa: S104


@pytest.mark.parametrize("port", ["-1", "65536", "http"])
def test_an_impossible_portal_port_is_rejected(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    monkeypatch.setenv("MENDWORK_PORTAL_PORT", port)

    with pytest.raises(ValidationError, match="portal_port"):
        Settings(_env_file=None)


def test_settings_are_frozen() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(ValidationError):
        # mypy rejects this statically; the assertion is that runtime does too.
        settings.log_level = LogLevel.DEBUG  # type: ignore[misc]


def test_the_workflow_size_limit_defaults_to_one_mebibyte() -> None:
    assert Settings(_env_file=None).workflow_max_bytes == 1024 * 1024


@pytest.mark.parametrize("limit", ["4095", "16777217", "big"])
def test_an_unreasonable_workflow_size_limit_is_rejected(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setenv("MENDWORK_WORKFLOW_MAX_BYTES", limit)

    with pytest.raises(ValidationError, match="workflow_max_bytes"):
        Settings(_env_file=None)
