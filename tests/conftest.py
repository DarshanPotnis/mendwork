"""Shared fixtures.

``configure_logging`` mutates process-wide state (structlog's defaults and the root
logger), so every test gets it put back to avoid order-dependent runs.
"""

import logging
from collections.abc import Callable, Iterator

import click
import pytest
import structlog
from hypothesis import settings
from typer.testing import Result

# Deterministic property tests: the same examples on every run and machine, no example
# database carrying state between runs, and no wall-clock deadline to flake on slow CI.
settings.register_profile(
    "mendwork", derandomize=True, database=None, deadline=None, max_examples=100
)
settings.load_profile("mendwork")


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
