"""Shared fixtures.

``configure_logging`` mutates process-wide state (structlog's defaults and the root
logger), so every test gets it put back to avoid order-dependent runs.
"""

import logging
from collections.abc import Callable, Iterator

import click
import pytest
import structlog
from typer.testing import Result


@pytest.fixture
def plain_stdout() -> Callable[[Result], str]:
    """Return a CLI result's stdout with ANSI styling removed.

    Typer renders help through Rich, which emits styling whenever GITHUB_ACTIONS,
    FORCE_COLOR, or PY_COLORS is set — read at import time, so it cannot be turned off
    from inside a test run. Assertions match text, not the terminal's mood.
    """

    def strip_styling(result: Result) -> str:
        return click.unstyle(result.stdout)

    return strip_styling


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    try:
        yield
    finally:
        structlog.reset_defaults()
        root.handlers = handlers
        root.setLevel(level)
