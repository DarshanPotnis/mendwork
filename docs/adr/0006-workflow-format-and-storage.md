# 6. Workflow format, risk by consequence, and no-overwrite storage

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

Phase 2 fixes the workflow format that every later phase reads and writes: the replayer
executes it, the recorder produces it, the healer patches it, and the benchmark scores it.
A format mistake found after Phase 4 means migrating recorded workflows, so the review
asked for the failure modes up front:

- a click carrying a value, or a password typed from a literal;
- a typo in an input name that only fails halfway through a run;
- two identical "View" buttons on one page;
- a YAML file where `no` is false and a repeated key silently wins;
- two writers publishing the same version number;
- a checkpoint that passes before the action has done anything.

The review also found two design gaps outside the format itself:

- The chaos portal's `dangerous_rename` keeps the target's `id` and `data-testid`. Verified
  in the browser on all seven primary targets: only the label changes (for example
  `#download-csv` becomes "Delete data"). A Rung 0 that trusts any selector that resolves
  to exactly one element would click it.
- ARCHITECTURE.md §5 called filters SAFE, while §8 made every form submission
  IRREVERSIBLE. A filter form is both.

## Decision

### Actions and steps

- **No DOWNLOAD action.** A download is a CLICK verified by a `download_completed`
  checkpoint. Whether a click downloads is an outcome, not a different gesture, and a
  separate action would duplicate CLICK's targeting and healing.
- **One model per action**, as a discriminated union on `action`:

  | Action | Target | Value | Other |
  |---|---|---|---|
  | navigate | none | literal http(s) URL or url input | |
  | click | required | none | |
  | fill | required | literal, input, or secret | credential rule |
  | select | required | literal or input (the option's visible label) | |
  | press | optional | none | `key`: `Modifier+…+Key` |

  A click cannot carry a value because the field does not exist on its model, and errors
  point at the offending field.

### Selectors and fingerprints

- **Selectors are a union on `strategy`**: `test_id`, `role_name`, `label`, `placeholder`,
  `text`, `css`.
  - Text-based strategies have `exact` (default true; false is Playwright's
    case-insensitive substring match).
  - A css selector must be plain CSS. `>>`, engine prefixes, and Playwright pseudo-classes
    are rejected, so the string also works with `querySelectorAll`.
- **Scoped selectors.** Every selector may declare `within: Selector`. The target is
  searched only inside the single element the scope resolves to, and every level must
  resolve to exactly one element.
  - **Depth limit: 2.** Two levels cover a control in a row of one of several tables.
    Deeper chains encode layout that `structural_path` already records, and each level is
    one more thing a release can break.
- **Fingerprint attributes are a typed allowlist:** `id`, `name`, `type`, `autocomplete`,
  `placeholder`, `aria_label`, `data_testid`, `href`.
  - `href` is a path only, because query strings and fragments carry session tokens.
  - `autocomplete` was added to the original list: after `type`, it is the strongest
    credential signal (`current-password`, `one-time-code`).
- **`bbox` is relative to the whole document**, not the viewport. Values stay within 0..1
  whatever the scroll position.

### Inputs, secrets, and credentials

- **Values are structured.** A value is written `{kind: input, name: x}`, not
  `inputs.x`. With a string form, a literal such as `"secrets.txt"` would be misread as a
  reference.
- **Declarations.** A workflow declares its inputs (`name`, `kind`: text | date | url,
  `required`, `default`, `description`) and its secret names.
  - Every reference must match a declaration, and every declaration must be used.
  - An input and a secret may not share a name.
  - Secrets are allowed only in FILL.
  - A NAVIGATE input must be of kind url.
  - Dates are `YYYY-MM-DD`; URLs are absolute http(s) with no embedded credentials.
- **Optional inputs declare a typed default.** `required: true` forbids a default and
  `required: false` requires one, validated like a run value. Steps are never skipped
  because an input is missing.
- **Credential rule.** A FILL whose target looks like a credential field must use a secret
  reference. A literal would be stored in the file, and an input in run history.
  - **Detection:** `detect_secret_field` is a pure function of the fingerprint, and the
    first matching rule decides:
    1. type `password`;
    2. a credential `autocomplete` value;
    3. non-credential input types (email, date, url, …) are never credentials;
    4. credential words, tokenized, in id, name, test id, aria-label, placeholder, label,
       or accessible name;
    5. a masked placeholder.
  - **Deliberately ignored:** `nearby_text`, because the email field sits next to the
    "Password" label.
  - **Errors lean strict.** Accepted false positives include "PIN code" (a postal code in
    India). Accepted false negatives include CSS-masked text fields, unlisted languages,
    and unlabelled fields.

### Checkpoints

- **Kinds:** `url_matches` (explicit `mode`: exact | prefix | regex), `element_visible`,
  `text_present`, `download_completed` (filename regex), `response_received` (URL match
  plus a status range), and `no_error_banner`.
- **Regexes use Python `re` syntax** and must match the whole string (`re.fullmatch`).
  They are compiled when the workflow loads.
- **`timeout_ms` is optional** (1–600000); absent means the runtime default.
  `no_error_banner` has no timeout: it is checked once, after the step's other checkpoints
  pass, because waiting for something not to appear would be a sleep.

### Versions

- **Numbering.** Version 1 has no parent and no change record. Every later version names
  `version − 1` as its parent and carries a `ChangeRecord` (`manual_edit` or `rollback`).
  A rollback may restore at most `version − 2`.
- **Deriving a child** keeps the parent unchanged, keeps the same step ids in the same
  order, and takes `created_at` from the Clock port.
  - Each kind of change has its own pure function (`edit_version`,
    `roll_back_version`) over one private core.
  - A generic "parent plus change record" function could not check that a rollback's
    content really is the restored version's, so it does not exist.
  - Phase 8 adds a heal variant and its function. Every `match` over `ChangeRecord` then
    fails type checking until it handles the new kind.
- **Stable step ids.** Every version of a workflow has the same ordered step ids. Adding,
  removing, or reordering steps needs a new ChangeRecord kind and its own ADR.

### Bounds and versioning of the format

- **`schema_version`.** Every file carries it, and an unsupported one fails with
  `UnsupportedSchemaVersion` before any other validation.
- **Format bounds** (text and list lengths, scope depth, timeouts) are constants in
  `engine/domain/limits.py`, not Settings. A file valid in one deployment must be valid
  in all of them.
  - The canonical dump omits fields at their default, so changing a bound or a default is
    a schema version change.
  - The only operational limit is the document size cap, `MENDWORK_WORKFLOW_MAX_BYTES`.

### Risk by consequence

- **Levels:**
  - **SAFE:** reads or navigates only (links, filters, downloads, opening details).
  - **CAUTION:** changes session or unsaved form state, reversibly (fills, sign in, sign
    out).
  - **IRREVERSIBLE:** changes stored data or affects others (submitting orders, paying,
    deleting, sending, saving settings).
- **Signals, not rules.** A form submission and danger keywords are signals for the
  classifier. When classification is unsure, it chooses the stricter level.
- **Authentication steps get at most one heal attempt (Phase 5).** These are steps that
  fill a credential or submit one. Repeated attempts with a wrong target, or a right
  target with wrong timing, can lock an account.

### Rung 0 identity check

- **An exact selector match is not automatically a success.** A selector that resolves to
  exactly one visible element must also match the fingerprint's role and accessible name.
- **Otherwise it is a drifted match.** A drifted match passes the same acceptance rules
  as a heal (danger keywords, risk policy, verification) before any action. "Download
  CSV" → "Export data" can pass; "Download CSV" → "Delete data" must abstain.
- **Phases.** Phase 3 has no acceptance rules yet, so it stops on a drifted match with the
  evidence. Phase 5 routes drifted matches through heal acceptance.

### YAML lives in adapters; storage never overwrites

- **The engine never imports YAML.** It validates plain decoded documents, and
  import-linter forbids `yaml` in the engine.
- **Strict loading.** `adapters/workflow_yaml` builds documents from PyYAML's event stream
  itself:
  - It rejects duplicate keys, anchors, aliases, explicit tags, non-text keys, multiple
    documents, and nesting deeper than 32 levels.
  - It checks the size cap before parsing.
  - It resolves unquoted scalars under a narrow rule set: null, true/false, decimal
    integers, and decimals with a point. That set is a strict subset of what PyYAML's
    YAML 1.1 dumper leaves unquoted as non-text, so every string it writes reads back as a
    string. A property test found and pinned the `-.5` and `1.0e5` edge cases.
- **Errors carry positions.** The loader records the position of every key and list item.
  - Validation runs in pydantic's strict JSON mode.
  - Errors are mapped back to document paths, with union-tag segments dropped, and
    rewritten in plain English. They never echo input values.
  - Each issue gets the line of its nearest existing path; missing fields point at their
    parent.
- **Deterministic dumping.** Field order follows the model, nothing is aliased, lines
  never wrap, and `dump(load(dump(x)))` is byte-identical to `dump(x)`.
- **No-overwrite publish.** `FileWorkflowStore` stores `<root>/<workflow_id>/v0001.yaml`,
  one file per version, and derives the latest version from the files. Publishing takes
  six steps:
  1. Validate the id, and refuse symlinked directories.
  2. Require the parent version to be stored.
  3. Write a temporary file in the same directory, then fsync it.
  4. `link(2)` it to the final name. link fails with EEXIST and never replaces a file.
  5. Unlink the temporary file on every path.
  6. fsync the directory.

  Two writers racing for one number produce one file and one `VersionConflict`.
  Re-publishing byte-identical content is a no-op, so a retry after an ambiguous failure
  is safe.
- **Reads are strict.** Non-canonical names, gaps, symlinked files, and files that
  disagree with their path are integrity errors. Dotfiles (temporary files) are ignored.

## Alternatives considered

- **Keep DOWNLOAD as an action.** It is explicit, but duplicates CLICK's targeting and
  healing, and a click that unexpectedly downloads would be a different action at
  replay time. Rejected.
- **One Step model with runtime shape checks.** Simpler to consume, but the JSON Schema
  could not tell editors which fields each action takes, and errors would point at the
  step rather than the field. Rejected.
- **Scoping through Playwright's `>>` chains in css strings.** No new field, but it
  bypasses the depth limit and the per-level exactly-one rule, and page scripts cannot run
  it. Rejected.
- **Skipping steps whose optional input is missing.** It avoids defaults, but a skipped
  fill changes what a submit sends. Rejected for typed defaults.
- **ruamel.yaml.** It preserves comments on round trip, but it types poorly under mypy
  strict and has no clean way to refuse aliases. Rejected; hand-written examples keep
  their comments because the store writes canonical files only for new versions.
- **`os.rename` or `os.replace` for publishing.** Both overwrite an existing file on POSIX.
  `renameat2(RENAME_NOREPLACE)` and `renamex_np(RENAME_EXCL)` are platform-specific and
  not in the standard library. Writing the final name directly with `O_EXCL` exposes
  partial files to readers and leaves them after a crash. Rejected for link-then-unlink.
- **A `latest` pointer file.** Cheaper to read, but a second write that can disagree with
  the version files. Rejected.
- **Trusting any exact Rung 0 match.** Free and simple, but it clicks "Delete data" on the
  portal's own abstain case. Rejected.

## Consequences

- Editors get per-action autocomplete from `schemas/workflow.schema.json`. A test fails
  when the committed schema is stale, and property tests prove every valid version
  satisfies it.
- `mendwork validate` reports every problem as `file:line:column: path (step id): message`.
- **Regular expression denial of service (ReDoS).** Checkpoint patterns are
  author-supplied, and Python `re` has no timeout. Pattern length is bounded, but a
  catastrophic-backtracking pattern could still stall a worker. Before multi-tenant
  execution (Phase 10), patterns from workspaces must either:
  - be evaluated by a linear-time engine (RE2, rejecting backreferences and lookaround at
    upload, with its own ADR and dependency); or
  - run in the worker under a hard CPU time limit, so one pattern cannot block other runs.
- The store requires hard links. On a file system without them, publishing fails loudly
  instead of falling back to an overwriting rename.
- On macOS, `fsync` does not flush the drive's write cache. Production targets Linux.
- A crash between link and unlink leaves a hidden temporary file that readers ignore and
  nothing removes.
- Credential detection sees only the fingerprint. Masked fields with neutral labels are
  missed, and Phase 4's recorder, which sees the live element, must apply its own check
  too.
- Checkpoints cannot reference inputs. A workflow whose checkpoint text depends on a date
  must use literal dates, as the download example does, until a schema version adds
  templating.
- Changing the steps of a workflow (adding, removing, reordering) is not yet a version
  change.
- Phase 3 must implement the Rung 0 identity check, and Phase 5 the one-attempt cap for
  authentication steps.
