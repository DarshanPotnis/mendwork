"""Shared fixtures.

``configure_logging`` mutates process-wide state (structlog's defaults and the root
logger), so every test gets it put back to avoid order-dependent runs.
"""

import logging
from collections.abc import Iterator

import pytest
import structlog


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
