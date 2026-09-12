"""The SecretResolver port: secret values, supplied only at the moment of use."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import SecretStr

from mendwork.engine.domain.identifiers import SecretName


class SecretResolver(Protocol):
    """Looks secrets up by name. Values are wrapped so they never print by accident."""

    async def missing(self, names: Sequence[SecretName]) -> tuple[SecretName, ...]:
        """The names that cannot be resolved (absent or empty), so a run fails before it starts."""
        ...

    async def resolve(self, name: SecretName) -> SecretStr:
        """The value of one secret. Raises SecretUnavailable if it is absent or empty."""
        ...
