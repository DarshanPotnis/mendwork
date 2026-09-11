"""Logging setup: structlog for Mendwork's own logs and for third-party libraries.

Everything goes through one pipeline so that a single redaction processor covers both,
and everything goes to stderr, because stdout is reserved for machine-readable command
output. Rendering is JSON in production and human-readable everywhere else.
"""

import logging
import sys

import structlog
from structlog.typing import Processor

from mendwork.engine.safety.redaction import make_redaction_processor
from mendwork.settings import Environment, Settings


def configure_logging(settings: Settings) -> None:
    """Install the logging pipeline for this process, replacing any previous one."""
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        make_redaction_processor(settings.sensitive_key_fragments),
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # ExtraAdder lifts a stdlib record's `extra=` fields into the event dict before
        # redaction runs, so a third-party library cannot log a secret past the filter.
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *shared_processors],
        processors=_renderers(settings.environment),
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.value)


def _renderers(environment: Environment) -> list[Processor]:
    if environment is Environment.PRODUCTION:
        return [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            # A rendered traceback string, not a structured one: structured tracebacks
            # capture frame locals, which is exactly where unredacted secrets sit.
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    return [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
    ]
