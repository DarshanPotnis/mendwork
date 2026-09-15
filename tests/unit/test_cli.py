"""The mendwork CLI reports its version and installs logging at startup."""

import logging
import sys
from collections.abc import Callable
from importlib.metadata import version as package_version

import pytest
import structlog
import typer
from typer.testing import CliRunner, Result

from mendwork.apps.cli.main import app, main
from mendwork.engine.safety.secret_scrub import SecretScrubber


def test_version_option_prints_the_installed_version(
    plain_stdout: Callable[[Result], str],
) -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert plain_stdout(result).strip() == f"mendwork {package_version('mendwork')}"


def test_help_documents_the_version_option(plain_stdout: Callable[[Result], str]) -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--version" in plain_stdout(result)


def test_an_unknown_command_fails_loudly() -> None:
    result = CliRunner().invoke(app, ["teleport"])

    assert result.exit_code == 2


def test_startup_installs_the_logging_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENDWORK_LOG_LEVEL", "DEBUG")
    ctx = typer.Context(typer.main.get_command(app))

    main(ctx, version=False)

    assert isinstance(ctx.obj, SecretScrubber)
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr
    assert isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter)
