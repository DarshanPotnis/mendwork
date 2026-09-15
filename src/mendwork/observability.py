"""Logging setup: structlog for Mendwork's own logs and for third-party libraries.

Everything goes through one pipeline so that a single redaction processor covers both,
and everything goes to stderr, because stdout is reserved for machine-readable command
output. Rendering is JSON in production and human-readable everywhere else.

Redaction happens twice. Values under sensitive field names are replaced before rendering; then
the rendered line, traceback included, is scrubbed of every secret value this process has
resolved, in every encoding the scrubber covers, so a secret that reaches a log inside an error
message or under an innocent name is removed too (ADR 0011).
"""

import logging
import sys

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from mendwork.engine.safety.redaction import make_redaction_processor
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Environment, Settings


def configure_logging(settings: Settings, scrubber: SecretScrubber) -> None:
    """Install the logging pipeline for this process, replacing any previous one.

    ``scrubber`` is the process's: every secret the process resolves must be registered with it
    (``RegisteringSecretResolver``).
    """
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
        processors=_renderers(settings.environment, scrubber),
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.value)


def _renderers(environment: Environment, scrubber: SecretScrubber) -> list[Processor]:
    if environment is Environment.PRODUCTION:
        return [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            # A rendered traceback string, not a structured one: structured tracebacks
            # capture frame locals, which is exactly where unredacted secrets sit.
            structlog.processors.format_exc_info,
            _scrubbed(structlog.processors.JSONRenderer(), scrubber),
        ]
    return [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        # The default formatter renders tracebacks with every frame's local variables, and
        # a resolved secret is exactly such a variable. Plain tracebacks carry no locals.
        _scrubbed(
            structlog.dev.ConsoleRenderer(
                colors=sys.stderr.isatty(), exception_formatter=structlog.dev.plain_traceback
            ),
            scrubber,
        ),
    ]


def _scrubbed(renderer: Processor, scrubber: SecretScrubber) -> Processor:
    """The renderer, with every secret the process resolved removed from what it renders."""

    def render(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> str:
        rendered = renderer(logger, method_name, event_dict)
        text = rendered if isinstance(rendered, str) else str(rendered)
        return scrubber.scrub_outbound(text)

    return render
