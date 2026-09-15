"""The logging pipeline renders per environment, writes to stderr, and redacts secrets."""

import json
import logging

import pytest
import structlog
from pydantic import SecretStr

from mendwork.engine.safety.redaction import DEFAULT_SENSITIVE_KEY_FRAGMENTS, REDACTED
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.observability import configure_logging
from mendwork.settings import Environment, LogLevel, Settings

SECRET = "Zq7-leak/probe &4421"


def configure(settings: Settings, scrubber: SecretScrubber | None = None) -> None:
    configure_logging(settings, scrubber or SecretScrubber())


def _settings(
    *,
    environment: Environment = Environment.DEVELOPMENT,
    log_level: LogLevel = LogLevel.INFO,
    sensitive_key_fragments: frozenset[str] = DEFAULT_SENSITIVE_KEY_FRAGMENTS,
) -> Settings:
    return Settings(
        _env_file=None,
        environment=environment,
        log_level=log_level,
        sensitive_key_fragments=sensitive_key_fragments,
    )


def test_a_marked_field_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure(_settings())

    structlog.get_logger("test").info("credentials submitted", password="hunter2", user="ada")

    captured = capsys.readouterr()
    assert "hunter2" not in captured.err
    assert "[REDACTED]" in captured.err
    assert "ada" in captured.err


def test_redaction_is_case_insensitive_and_recurses(capsys: pytest.CaptureFixture[str]) -> None:
    configure(_settings(environment=Environment.PRODUCTION))

    structlog.get_logger("test").info(
        "provider configured",
        provider={"API_Key": "sk-live-123", "sessions": [{"Auth_Token": "t-1"}]},
        # Domain models hold tuples, so tuples reach the log pipeline too.
        candidates=({"name": "Download CSV", "Cookie": "sid=1"},),
        attempts=2,
    )

    payload = json.loads(capsys.readouterr().err)
    assert payload["provider"] == {
        "API_Key": "[REDACTED]",
        "sessions": [{"Auth_Token": "[REDACTED]"}],
    }
    assert payload["candidates"] == [{"name": "Download CSV", "Cookie": "[REDACTED]"}]
    assert payload["attempts"] == 2


def test_third_party_logs_share_the_renderer_and_redaction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(_settings(environment=Environment.PRODUCTION))

    logging.getLogger("vendor.client").warning(
        "token refreshed", extra={"authorization": "Bearer abc", "status": 200}
    )

    payload = json.loads(capsys.readouterr().err)
    assert payload["event"] == "token refreshed"
    assert payload["logger"] == "vendor.client"
    assert payload["level"] == "warning"
    assert payload["authorization"] == "[REDACTED]"
    assert payload["status"] == 200


def test_the_sensitive_key_list_is_configurable(capsys: pytest.CaptureFixture[str]) -> None:
    configure(_settings(sensitive_key_fragments=frozenset({"session_id"})))

    structlog.get_logger("test").info("run started", session_id="s-1", password="hunter2")

    captured = capsys.readouterr()
    assert "s-1" not in captured.err
    assert "hunter2" in captured.err


def test_development_renders_for_humans_and_production_renders_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(_settings(environment=Environment.DEVELOPMENT))
    structlog.get_logger("test").info("run started")
    development = capsys.readouterr().err
    with pytest.raises(json.JSONDecodeError):
        json.loads(development)
    assert "run started" in development

    configure(_settings(environment=Environment.PRODUCTION))
    structlog.get_logger("test").info("run started")
    assert json.loads(capsys.readouterr().err)["event"] == "run started"


def test_logs_never_reach_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure(_settings())

    structlog.get_logger("test").info("run started")
    logging.getLogger("vendor.client").error("connection lost")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "run started" in captured.err
    assert "connection lost" in captured.err


def test_events_below_the_configured_level_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure(_settings(log_level=LogLevel.WARNING))

    log = structlog.get_logger("test")
    log.info("not important")
    log.warning("important")

    captured = capsys.readouterr()
    assert "not important" not in captured.err
    assert "important" in captured.err


@pytest.mark.parametrize("environment", [Environment.DEVELOPMENT, Environment.PRODUCTION])
def test_a_secret_the_process_resolved_is_removed_from_every_line_by_value(
    capsys: pytest.CaptureFixture[str], environment: Environment
) -> None:
    scrubber = SecretScrubber()
    configure(_settings(environment=environment), scrubber)
    scrubber.register(SecretStr(SECRET))
    log = structlog.get_logger("test")

    log.warning(f"sign-in refused for {SECRET}", url=f"https://a.test/?p={SECRET}")
    try:
        raise ValueError(f"the page echoed {SECRET}")
    except ValueError:
        log.exception("step crashed")
    logging.getLogger("vendor.client").error("request body %s", json.dumps({"p": SECRET}))

    err = capsys.readouterr().err
    assert "Zq7-leak" not in err
    assert "4421" not in err
    assert REDACTED in err
    assert "ValueError" in err
    assert "vendor.client" in err or "request body" in err
