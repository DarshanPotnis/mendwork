"""The SecretResolver port backed by the process environment."""

from collections.abc import Mapping, Sequence

from pydantic import SecretStr

from mendwork.adapters.secrets_env.naming import secret_variable_name
from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.errors import SecretUnavailable


class EnvSecretResolver:
    """Reads ``MENDWORK_SECRET_<NAME>`` when a secret is needed, never earlier.

    An empty variable counts as missing: typing nothing into a password field is never
    what a workflow means.
    """

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    async def missing(self, names: Sequence[SecretName]) -> tuple[SecretName, ...]:
        return tuple(name for name in names if not self._environ.get(secret_variable_name(name)))

    async def resolve(self, name: SecretName) -> SecretStr:
        variable = secret_variable_name(name)
        value = self._environ.get(variable)
        if not value:
            raise SecretUnavailable(
                f"secret '{name}' is not available; set the environment variable {variable}",
                name=name,
                variable=variable,
            )
        return SecretStr(value)
