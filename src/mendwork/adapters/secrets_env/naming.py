"""How secrets are named in the environment: ``MENDWORK_SECRET_<UPPER_SNAKE_NAME>``.

The ``MENDWORK_SECRET_`` namespace is reserved. Settings ignores well-formed names in it
and rejects malformed ones, so a secret variable the resolver could never find fails at
startup instead of at the moment of use.
"""

import re
from typing import Final

from mendwork.engine.domain.identifiers import SecretName, is_slug

SECRET_ENV_PREFIX: Final = "MENDWORK_SECRET_"  # noqa: S105 - a variable name prefix, not a value
VARIABLE_NAMING_HINT: Final = (
    "a secret variable is MENDWORK_SECRET_ followed by the secret's name in UPPER_SNAKE_CASE, "
    "such as MENDWORK_SECRET_PORTAL_PASSWORD"
)
_UPPER_SNAKE: Final = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")


def secret_variable_name(name: SecretName) -> str:
    """The environment variable that holds a secret."""
    return f"{SECRET_ENV_PREFIX}{name.upper()}"


def is_secret_namespace(variable: str) -> bool:
    """Whether a variable claims the reserved namespace, in any letter case."""
    return variable.upper().startswith(SECRET_ENV_PREFIX)


def is_valid_secret_variable(variable: str) -> bool:
    """Whether a variable names a secret exactly as the resolver will look it up."""
    if not variable.startswith(SECRET_ENV_PREFIX):
        return False
    suffix = variable.removeprefix(SECRET_ENV_PREFIX)
    return _UPPER_SNAKE.fullmatch(suffix) is not None and is_slug(suffix.lower())
