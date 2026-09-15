"""Registering every secret a process resolves, so its logs can be scrubbed of them by value.

Log redaction by field name (``redaction``) catches a secret logged under a name that says so. A
secret can also reach a log line inside an error message, a URL, or a traceback. Wrapping the
process's SecretResolver in a RegisteringSecretResolver hands each value to the scrubber the log
pipeline reads (``mendwork.observability``) at the moment it is resolved, before any code holding
it could log it.
"""

from collections.abc import Sequence

from pydantic import SecretStr

from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.ports.secrets import SecretResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber


class RegisteringSecretResolver:
    """A SecretResolver that registers every value it resolves with a scrubber."""

    def __init__(self, inner: SecretResolver, scrubber: SecretScrubber) -> None:
        self._inner = inner
        self._scrubber = scrubber

    async def missing(self, names: Sequence[SecretName]) -> tuple[SecretName, ...]:
        return await self._inner.missing(names)

    async def resolve(self, name: SecretName) -> SecretStr:
        value = await self._inner.resolve(name)
        self._scrubber.register(value)
        return value
