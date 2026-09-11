"""Process configuration, read from the environment with the ``MENDWORK_`` prefix.

This is the only place a default may live. Apps read ``Settings`` and hand the values
the engine needs to its constructors, so the engine never reaches for configuration
itself and stays trivially testable with explicit values.
"""

import os
from collections.abc import Iterable, Mapping
from difflib import get_close_matches
from enum import StrEnum
from typing import Any, Final

from pydantic import model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from mendwork.engine.safety.redaction import DEFAULT_SENSITIVE_KEY_FRAGMENTS

ENV_PREFIX: Final = "MENDWORK_"


class Environment(StrEnum):
    """Where this process is running, which decides how logs are rendered."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Minimum severity the logging pipeline emits."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def describe_unknown_variables(
    field_names: Iterable[str], variable_names: Iterable[str]
) -> str | None:
    """Describe every ``MENDWORK_`` variable that matches no field, or None if all match.

    pydantic-settings ignores a prefixed variable it does not recognise, so a typo
    silently does nothing. Configuration that looks applied but is not is worse than a
    failure to start, hence this check.
    """
    valid_suffixes = sorted(name.upper() for name in field_names)
    valid = [f"{ENV_PREFIX}{suffix}" for suffix in valid_suffixes]
    unknown = sorted(
        name
        for name in {variable.upper() for variable in variable_names}
        if name.startswith(ENV_PREFIX) and name not in valid
    )
    if not unknown:
        return None

    lines = []
    for name in unknown:
        # Compare without the shared prefix, which would otherwise make every name
        # look similar to every other one.
        closest = get_close_matches(name.removeprefix(ENV_PREFIX), valid_suffixes, n=1)
        hint = (
            f"did you mean {ENV_PREFIX}{closest[0]}?"
            if closest
            else f"valid names are {', '.join(valid)}"
        )
        lines.append(f"  {name} ({hint})")
    return "unrecognised environment variables:\n" + "\n".join(lines)


class _OwnDotEnvSource(PydanticBaseSettingsSource):
    """A .env source that ignores the keys belonging to other tools.

    From Phase 10 the same .env file is read by docker compose, so it holds names like
    POSTGRES_PASSWORD that are none of our business. Keys that are ours but match no
    field are kept, so that validation can report them as typos rather than drop them.
    """

    def __init__(self, dotenv: PydanticBaseSettingsSource) -> None:
        super().__init__(dotenv.settings_cls)
        self._dotenv = dotenv

    # Any here mirrors the library's own abstract signature, which is dynamic by nature.
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._dotenv.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        return {
            name: value
            for name, value in self._dotenv().items()
            if name in self.settings_cls.model_fields or name.upper().startswith(ENV_PREFIX)
        }


class Settings(BaseSettings):
    """Runtime configuration for every Mendwork process."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    sensitive_key_fragments: frozenset[str] = DEFAULT_SENSITIVE_KEY_FRAGMENTS

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep the default precedence — init, then environment, then .env — and filter .env."""
        return (
            init_settings,
            env_settings,
            _OwnDotEnvSource(dotenv_settings),
            file_secret_settings,
        )

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_environment_variables(cls, data: object) -> object:
        # A prefixed .env key that matched no field arrives here under its raw name,
        # whereas the environment source drops its unmatched names before this point.
        # Both are collected so that one error reports every typo.
        names = list(os.environ)
        if isinstance(data, Mapping):
            names.extend(str(name) for name in data if str(name).upper().startswith(ENV_PREFIX))
        problem = describe_unknown_variables(cls.model_fields, names)
        if problem is not None:
            raise ValueError(problem)
        return data
