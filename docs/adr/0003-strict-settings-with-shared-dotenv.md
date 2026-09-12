# 3. Strict settings over a shared .env file

- **Status:** Accepted
- **Date:** 2026-09-10

## Context

A misspelled setting must stop the process. `MENDWORK_LOG_LEVLE=DEBUG` that is silently
ignored leaves an operator believing configuration is applied when it is not — worse
than a failure to start, because the system keeps running under the wrong settings.

Settings arrive from two places: the process environment and a `.env` file. They failed
differently. pydantic-settings drops an unrecognised prefixed *environment variable*
without comment, so we added our own scan. A `.env` file behaved the opposite way: under
`extra="forbid"` the dotenv source passes every key it cannot match to a field into the
validated data, so pydantic rejected them with `Extra inputs are not permitted` — no
suggestion, lower-cased, a different shape from our own message.

That strictness is also wrong for `.env` specifically. From Phase 10, docker compose
reads the same file, so it will hold `POSTGRES_PASSWORD` and `COMPOSE_PROJECT_NAME`.
Settings must load anyway: the `MENDWORK_` namespace is ours to police, and nothing else
in the file is.

## Decision

`_OwnDotEnvSource` wraps the dotenv source through `settings_customise_sources`, the
documented extension point, and filters its output: keys that matched a field are kept,
keys that did not are kept only when they start with `MENDWORK_`.

Foreign keys therefore vanish before validation. Ours survive under their raw name —
`mendwork_log_levle` rather than `log_levle` — because a matched key reaches the data
with its prefix stripped, so a surviving prefixed name is by definition one we could not
match. A `mode="before"` validator collects those names together with the process
environment's and passes both to the one pure function, `describe_unknown_variables`, so
a single `ValidationError` reports every typo from both sources in one message.

`extra="forbid"` stays. An unknown keyword argument reaches validation without a prefix,
is not ours to report, and is rejected by pydantic as before. The sources are returned in
their original order, so the environment still overrides `.env`.

## Alternatives considered

- **`dotenv_filtering="only_existing"`.** One config line, and it ignores every unmatched
  key — including our own typos, which is the behaviour we are trying to remove. The
  validator would then have to read the `.env` file itself, and it cannot: a validator
  sees no `_env_file` override, so the check would silently target the wrong file.
- **`dotenv_filtering="match_prefix"`.** Ignores foreign keys in one line, but strips the
  prefix from unmatched keys too, so `MENDWORK_LOG_LEVLE` arrives as `log_levle` —
  indistinguishable from a typo'd keyword argument. Reporting `.env` typos in our format
  while keeping code callers strict would no longer be possible. Rejected: it collapses
  two cases we need to tell apart.
- **`extra="ignore"` plus an explicit `__init__` guard on keyword arguments.** Works, but
  loosens the model globally to re-tighten it by hand, and forces `**values: Any` at a
  constructor we would rather keep typed.
- **Reading the `.env` file ourselves with `dotenv_values`.** Duplicates precedence,
  encoding, and case-sensitivity rules that the library already implements, and is blind
  to `_env_file` overrides.

## Consequences

- One message, one format, one error for both sources, produced by a pure function that
  derives valid names from the model's fields and so cannot drift from it.
- A `.env` shared with docker compose loads, and code callers get no slack from that.
- We depend on observable pydantic-settings behaviour: that the dotenv source passes
  unmatched keys through under their raw name. If a future version drops them instead,
  `.env` typos would stop being caught. `test_a_misspelled_variable_in_a_dotenv_file_is_rejected`
  pins it, so the change surfaces as a failing test rather than a silent loss of the
  guarantee. That test is the reason this decision is safe to depend on.
- The wrapper must stay in the dotenv slot of the returned tuple. Moving it would change
  precedence, which `test_the_environment_overrides_the_dotenv_file` guards.
- A foreign key that begins with `MENDWORK_` would be reported as a typo. Accepted: that
  namespace belongs to us, and the alternative is not noticing our own mistakes.
- **`MENDWORK_SECRET_` is reserved (Phase 3, ADR 0007).** Secret values are read by the
  secret resolver at the moment of use, never by Settings. The check carves the namespace
  out without weakening typo detection:
  - an environment variable in the namespace is accepted only when named exactly as the
    resolver looks it up: `MENDWORK_SECRET_` plus the secret name in UPPER_SNAKE_CASE, so
    `MENDWORK_SECRET_bad-name`, `MENDWORK_SECRET_portal_password`, and `MENDWORK_SECRET_`
    are rejected with the naming rule;
  - every other name follows the original path, so `MENDWORK_LOG_LEVLE` still fails with a
    suggestion and `MENDWORK_SECRETS_X` is an ordinary typo;
  - any namespace key in `.env` is rejected ("secrets are read from the process environment
    only"), because the dotenv source lower-cases keys and the resolver would never see it;
  - no Settings field may start with `secret_`, and a test enforces it;
  - Settings hides input values in its errors, so a rejected value is never echoed.
  `tests/unit/test_settings_secret_variables.py` pins each of these.
