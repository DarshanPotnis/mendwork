"""Process configuration, read from the environment with the ``MENDWORK_`` prefix.

This is the only place a default may live. Apps read ``Settings`` and hand the values
the engine needs to its constructors, so the engine never reaches for configuration
itself and stays trivially testable with explicit values.

``MENDWORK_SECRET_`` is a reserved namespace: secret values, read by the secret resolver
at the moment of use, never by Settings. See ADR 0003.
"""

import os
from collections.abc import Iterable, Mapping
from difflib import get_close_matches
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Self

from pydantic import Field, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from mendwork.adapters.secrets_env.naming import (
    VARIABLE_NAMING_HINT,
    is_secret_namespace,
    is_valid_secret_variable,
)
from mendwork.engine.domain.heals import FeatureName
from mendwork.engine.healing.config import acceptance_problems
from mendwork.engine.safety.redaction import DEFAULT_SENSITIVE_KEY_FRAGMENTS
from mendwork.settings_model import ModelSettings

ENV_PREFIX: Final = "MENDWORK_"
# Danger words mark stored data changing or other people affected. "remove" and its kin are
# also soft verbs: acting on view state ("Remove filter") they change nothing stored.
DEFAULT_DANGER_WORDS: Final = frozenset(
    {
        "approve",
        "archive",
        "buy",
        "cancel",
        "checkout",
        "confirm",
        "deactivate",
        "delete",
        "destroy",
        "erase",
        "pay",
        "publish",
        "purchase",
        "purge",
        "reject",
        "remove",
        "reset",
        "revoke",
        "save",
        "send",
        "submit",
        "transfer",
        "unsubscribe",
    }
)
DEFAULT_SOFT_VERBS: Final = frozenset({"remove", "reset"})
DEFAULT_VIEW_STATE_NOUNS: Final = frozenset(
    {"column", "columns", "filter", "filters", "search", "selection", "sort", "sorting"}
)
DEFAULT_READ_WORDS: Final = DEFAULT_VIEW_STATE_NOUNS | frozenset(
    {
        "back",
        "collapse",
        "details",
        "download",
        "expand",
        "export",
        "find",
        "open",
        "preview",
        "print",
        "refresh",
        "show",
        "view",
    }
)
DEFAULT_SESSION_PHRASES: Final = frozenset(
    {"log in", "log out", "login", "logout", "sign in", "sign out"}
)
SECRETS_NOT_IN_DOTENV: Final = "secrets are read from the process environment only, never from .env"
_MAX_TIMEOUT_MS: Final = 600_000


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
    field_names: Iterable[str],
    variable_names: Iterable[str],
    dotenv_names: Iterable[str] = (),
) -> str | None:
    """Describe every ``MENDWORK_`` variable that matches no field, or None if all match.

    pydantic-settings ignores a prefixed variable it does not recognise, so a typo
    silently does nothing. Configuration that looks applied but is not is worse than a
    failure to start, hence this check.

    Environment variables in the reserved ``MENDWORK_SECRET_`` namespace are accepted
    only when named exactly as the secret resolver looks them up. In a .env file that
    namespace is always refused, because the resolver reads only the process environment.
    """
    valid_suffixes = sorted(name.upper() for name in field_names)
    valid = {f"{ENV_PREFIX}{suffix}" for suffix in valid_suffixes}
    problems: dict[str, str] = {}

    for name in set(variable_names):
        upper = name.upper()
        if not upper.startswith(ENV_PREFIX):
            continue
        if is_secret_namespace(name):
            if not is_valid_secret_variable(name):
                problems[name] = VARIABLE_NAMING_HINT
        elif upper not in valid:
            problems[upper] = _typo_hint(upper, valid_suffixes)

    for name in set(dotenv_names):
        upper = name.upper()
        if is_secret_namespace(name):
            problems[upper] = SECRETS_NOT_IN_DOTENV
        elif upper.startswith(ENV_PREFIX) and upper not in valid:
            problems[upper] = _typo_hint(upper, valid_suffixes)

    if not problems:
        return None
    lines = [f"  {name} ({hint})" for name, hint in sorted(problems.items())]
    return "unrecognised environment variables:\n" + "\n".join(lines)


def _typo_hint(name: str, valid_suffixes: list[str]) -> str:
    # Compare without the shared prefix, which would otherwise make every name look similar
    # to every other one.
    closest = get_close_matches(name.removeprefix(ENV_PREFIX), valid_suffixes, n=1)
    if closest:
        return f"did you mean {ENV_PREFIX}{closest[0]}?"
    return "valid names are " + ", ".join(f"{ENV_PREFIX}{suffix}" for suffix in valid_suffixes)


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


class Settings(ModelSettings):
    """Runtime configuration for every Mendwork process.

    Rung 3's model provider settings are declared in ``settings_model`` and read here with the
    same prefix, sources, and checks as every other setting.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        # A rejected value is not repeated back: it may sit next to a credential in .env.
        hide_input_in_errors=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    sensitive_key_fragments: frozenset[str] = DEFAULT_SENSITIVE_KEY_FRAGMENTS
    # Loopback by default: the chaos portal is a local test target, never a public site.
    portal_host: str = "127.0.0.1"
    # 0 asks the operating system for any free port.
    portal_port: int = Field(default=8765, ge=0, le=65535)
    # Largest workflow file accepted, checked before parsing: a denial-of-service guard.
    workflow_max_bytes: int = Field(default=1024 * 1024, ge=4096, le=16 * 1024 * 1024)

    # Replay timing. Waits are explicit conditions bounded by these, never sleeps.
    # Settle, Rung 0 resolution, pre-action waits, and the action: real apps render in
    # 1-5 s, and a removed control should not stall a run for longer than this.
    step_timeout_ms: int = Field(default=10_000, ge=1, le=_MAX_TIMEOUT_MS)
    # A waiting checkpoint with no timeout_ms of its own.
    checkpoint_timeout_ms: int = Field(default=10_000, ge=1, le=_MAX_TIMEOUT_MS)
    # One attempt of a navigate step: cold loads of real apps; Playwright's own default.
    navigation_timeout_ms: int = Field(default=30_000, ge=1, le=_MAX_TIMEOUT_MS)
    # A hung run releases its worker within ten minutes.
    run_timeout_ms: int = Field(default=600_000, ge=1_000, le=24 * 60 * 60 * 1000)
    # How long to wait for a quiet DOM before evaluating selectors on a busy one.
    settle_timeout_ms: int = Field(default=2_000, ge=1, le=60_000)
    # Consecutive animation frames without a DOM mutation; Playwright's stability rule.
    settle_quiet_frames: int = Field(default=2, ge=1, le=60)

    # Transient navigation retries: 3 attempts, pausing ~0.5 s then ~1 s, jitter keeping at
    # least half of each pause so concurrent workers do not retry in lockstep.
    navigation_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_initial_delay_ms: int = Field(default=500, ge=0, le=60_000)
    retry_max_delay_ms: int = Field(default=4_000, ge=0, le=300_000)
    retry_backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    retry_jitter_ratio: float = Field(default=0.5, ge=0.0, le=1.0)

    # Where run artifacts are written: <artifacts_dir>/runs/<run_id>/.
    artifacts_dir: Path = Path("artifacts")
    browser_headless: bool = True
    # Delay after every browser operation, for demo recordings; 0 in normal runs.
    browser_slow_mo_ms: int = Field(default=0, ge=0, le=10_000)
    # Recorded fingerprints' positions, and the portal's layout guarantees, assume 1280x720.
    viewport_width: int = Field(default=1280, ge=320, le=7680)
    viewport_height: int = Field(default=720, ge=240, le=4320)
    # Record a Playwright trace and keep it when a step fails, unless it could hold a secret.
    trace_on_failure: bool = True

    # Recording. A proposed checkpoint is verified when its step is recorded, against a page
    # that has already settled and been observed, so a proposal that fails rarely passes by
    # waiting longer; a person is waiting on it.
    record_checkpoint_timeout_ms: int = Field(default=1_000, ge=1, le=_MAX_TIMEOUT_MS)
    # How many ancestors of an ambiguous target are tried as a selector scope.
    record_scope_ancestors_max: int = Field(default=6, ge=1, le=20)
    # How many visible headings and landmarks are read before and after each recorded step.
    record_landmarks_max: int = Field(default=40, ge=1, le=500)

    # Risk classification by consequence (ARCHITECTURE.md §8): words read in a control's
    # name, lower case, one word each (session phrases may have several).
    risk_danger_words: frozenset[str] = DEFAULT_DANGER_WORDS
    risk_soft_verbs: frozenset[str] = DEFAULT_SOFT_VERBS
    risk_view_state_nouns: frozenset[str] = DEFAULT_VIEW_STATE_NOUNS
    risk_read_words: frozenset[str] = DEFAULT_READ_WORDS
    risk_session_phrases: frozenset[str] = DEFAULT_SESSION_PHRASES

    # Healing (ARCHITECTURE.md §7, ADR 0009). Rung 2 scores a live element as a weighted sum of
    # eight features; the weights sum to 1. The defaults follow from invariants, not from tuning
    # on any site, and the validator enforces the two that keep healing safe: role, tag/type,
    # nearby text, structural path, and position together stay below the accept threshold, and
    # the margin exceeds every nearby-text, structural-path, and position weight.
    heal_weight_name: float = Field(default=0.25, ge=0.0, le=1.0)
    heal_weight_label: float = Field(default=0.05, ge=0.0, le=1.0)
    heal_weight_attributes: float = Field(default=0.25, ge=0.0, le=1.0)
    heal_weight_role: float = Field(default=0.10, ge=0.0, le=1.0)
    heal_weight_tag_type: float = Field(default=0.05, ge=0.0, le=1.0)
    heal_weight_nearby_text: float = Field(default=0.10, ge=0.0, le=1.0)
    heal_weight_structural_path: float = Field(default=0.10, ge=0.0, le=1.0)
    heal_weight_position: float = Field(default=0.10, ge=0.0, le=1.0)
    # A candidate that lost one whole group of clues (its wording, say) still reaches 0.70;
    # one with neither wording nor identity attributes tops out at 0.45.
    heal_accept_threshold: float = Field(default=0.60, gt=0.0, le=1.0)
    # Above the largest single layout weight (0.10), below any identity group (0.15 to 0.30).
    heal_accept_margin: float = Field(default=0.15, gt=0.0, lt=1.0)
    # Unrelated labels agree by chance up to about 0.4; agreement at or below this is none.
    heal_name_similarity_floor: float = Field(default=0.5, ge=0.0, lt=1.0)
    # Position proximity reaches 0 at this distance, as a fraction of the document.
    heal_position_scale: float = Field(default=0.25, gt=0.0, le=2.0)
    # A page with more action-compatible visible elements than this is never healed: scoring
    # part of a page cannot prove the margin, so this is a capability limit, not only a
    # performance knob. Measured (ADR 0009): a large Wikipedia table has 1,769 candidates and a
    # scan costs about 2.6 ms each; 4,000 is 2.3x that page and scans twice within heal_timeout_ms.
    heal_candidates_max: int = Field(default=4_000, ge=1, le=100_000)
    # Healed targets one step may act on, counting those that fail verification.
    heal_max_attempts: int = Field(default=2, ge=1, le=5)
    # Authentication steps: repeated attempts can lock the account (ADR 0006).
    heal_authentication_max_attempts: int = Field(default=1, ge=0, le=1)
    # How many of the best candidates each heal attempt reports.
    heal_report_candidates: int = Field(default=5, ge=1, le=20)
    # All healing for one step, attempts and restores included.
    heal_timeout_ms: int = Field(default=30_000, ge=1, le=_MAX_TIMEOUT_MS)

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
        dotenv_names: list[str] = []
        if isinstance(data, Mapping):
            dotenv_names = [str(name) for name in data if str(name).upper().startswith(ENV_PREFIX)]
        problem = describe_unknown_variables(cls.model_fields, os.environ, dotenv_names)
        if problem is not None:
            raise ValueError(problem)
        return data

    @model_validator(mode="after")
    def _retry_delays_are_ordered(self) -> Self:
        if self.retry_max_delay_ms < self.retry_initial_delay_ms:
            raise ValueError("retry_max_delay_ms must not be less than retry_initial_delay_ms")
        return self

    def heal_weights(self) -> dict[FeatureName, float]:
        """The heal feature weights, keyed by feature."""
        return {
            FeatureName.NAME: self.heal_weight_name,
            FeatureName.LABEL: self.heal_weight_label,
            FeatureName.ATTRIBUTES: self.heal_weight_attributes,
            FeatureName.ROLE: self.heal_weight_role,
            FeatureName.TAG_TYPE: self.heal_weight_tag_type,
            FeatureName.NEARBY_TEXT: self.heal_weight_nearby_text,
            FeatureName.STRUCTURAL_PATH: self.heal_weight_structural_path,
            FeatureName.POSITION: self.heal_weight_position,
        }

    @model_validator(mode="after")
    def _heal_acceptance_is_safe(self) -> Self:
        problems = acceptance_problems(
            self.heal_weights(), self.heal_accept_threshold, self.heal_accept_margin
        )
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _risk_vocabulary_is_consistent(self) -> Self:
        words = (
            self.risk_danger_words
            | self.risk_soft_verbs
            | self.risk_view_state_nouns
            | self.risk_read_words
            | self.risk_session_phrases
        )
        if any(not word.strip() or word != word.lower() for word in words):
            raise ValueError("risk vocabulary entries must be non-blank and lower case")
        if not self.risk_soft_verbs <= self.risk_danger_words:
            raise ValueError("every risk soft verb must also be a risk danger word")
        if not self.risk_view_state_nouns <= self.risk_read_words:
            raise ValueError("every risk view-state noun must also be a risk read word")
        return self
