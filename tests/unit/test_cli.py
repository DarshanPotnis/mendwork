"""The mendwork CLI reports its version and installs logging at startup."""

import logging
import sys
from importlib.metadata import version as package_version

import pytest
import structlog
from typer.testing import CliRunner

from mendwork.apps.cli.main import app, main


def test_version_option_prints_the_installed_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"mendwork {package_version('mendwork')}"


def test_help_documents_the_version_option() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--version" in result.stdout


def test_an_unknown_command_fails_loudly() -> None:
    result = CliRunner().invoke(app, ["teleport"])

    assert result.exit_code == 2


def test_startup_installs_the_logging_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_LOG_LEVEL", "DEBUG")

    main(version=False)

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr
    assert isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter)
