# BUILD_PLAN.md — Phased build with Claude Code

## How to use this file

1. Unzip `mendwork.zip`, open the `mendwork` folder in VS Code (**File → Open Folder**), and accept the prompt to install the recommended extensions. Then run `git init` in the VS Code terminal.
2. **One phase per Claude Code session.** Paste the phase prompt, review the plan it proposes, approve or adjust, then let it build.
3. At the end of each phase, run `make check` yourself, read the diff, and commit. Don't start the next phase on a red build.
4. If Claude Code proposes something that contradicts `ARCHITECTURE.md`, push back or ask it to write an ADR. Don't let the design drift silently.

### Prerequisites (all free)

| Tool | Needed from | Notes |
|---|---|---|
| Git | Phase 0 | |
| Python 3.12+ | Phase 0 | |
| uv | Phase 0 | Python package manager — install instructions at docs.astral.sh/uv |
| Make | Phase 0 | macOS: Xcode Command Line Tools · Linux: `build-essential` · Windows: use WSL2 |
| Node.js LTS | Phase 1 | Dev tooling only, for TypeScript checks of plain JS |
| Ollama | Phase 6 | Optional, for a free local AI model |
| Docker | Phase 10 | Docker Desktop or Docker Engine |

**On Windows:** do everything inside WSL2 (Ubuntu) and open the folder with VS Code's WSL extension. That way Make, Docker, and Playwright behave exactly as they do in CI.

### What to watch for when reviewing its work

- Tests that only check "no exception was raised" instead of checking behaviour
- `# type: ignore`, `Any`, broad `except`, or `sleep`, without a stated reason
- Business logic inside `apps/` or vendor imports inside `engine/`
- Thresholds or model names hardcoded instead of coming from `Settings`
- "We can handle this later" — that means the phase isn't done

### Scope

- **Phases 0–9 = MVP.** Engine, CLI, safety, patching, and benchmark with an HTML scorecard. This is enough for the demo video, the README numbers, and your resume. Plan on roughly 3–4 weeks part-time; don't rush Phases 0–2, because everything sits on them.
- **Phases 10–12 = Platform.** Multi-company service, dashboard with race mode, and deployment.

---

## Phase 0 — Foundation

```
Read CLAUDE.md, ARCHITECTURE.md, and BUILD_PLAN.md fully before doing anything.

Phase 0 — Foundation. Goal: a production-grade, empty Python project skeleton. No product features.

Present a plan (every file you will create and why) and wait for my approval.

Deliver:
- pyproject.toml managed by uv, Python >=3.12, src layout, package `mendwork`, with empty subpackages matching ARCHITECTURE.md §4 (each with __init__.py and a one-line module docstring stating its responsibility).
- Tooling config: ruff (lint + format, strict rule set incl. bugbear, simplify, pyupgrade, isort), mypy --strict, pytest + pytest-asyncio + pytest-cov + hypothesis, import-linter with contracts:
  (1) mendwork.engine must not import mendwork.adapters or mendwork.apps
  (2) mendwork.engine must not import playwright, httpx, fastapi, sqlalchemy
  (3) mendwork.adapters must not import mendwork.apps
- Makefile targets exactly as listed in CLAUDE.md "Commands" (portal, jscheck, bench, live-providers may print "available from Phase N" for now); each fails loudly on error.
- Keep the existing .vscode/extensions.json and .vscode/settings.json tracked in git (do not ignore them); adjust settings only if they conflict with the tooling you configure.
- .pre-commit-config.yaml running ruff, mypy, import-linter.
- .github/workflows/ci.yml running `make check` on push and pull_request (Python 3.12, uv cache).
- src/mendwork/settings.py: pydantic-settings Settings (prefix MENDWORK_) with log level and environment name; .env.example; .gitignore excluding .env, artifacts, and caches.
- Logging setup module using structlog (JSON in production, pretty in dev) including a redaction processor hook (no secrets yet, but the hook exists and is tested).
- src/mendwork/engine/errors.py with the error hierarchy from ARCHITECTURE.md §5, each carrying structured context.
- Typer CLI entry point `mendwork` with `--version`.
- docs/adr/0001-record-architecture-decisions.md and docs/adr/0002-engine-as-library-ports-and-adapters.md.
- README.md with the problem statement, a status badge, and "Getting started" (make install, make check).

Tests: CLI --version works; Settings load from env; error classes carry context; redaction hook removes a marked field.

Acceptance:
- `make check` passes from a clean clone.
- Demonstrate import-linter catching a violation by temporarily adding an adapters import inside engine, show the failure output, then revert.
- No TODOs, no placeholder implementations.
```

---

## Phase 1 — Chaos portal

```
Phase 1 — Chaos portal: the demo target and benchmark ground truth.
Read ARCHITECTURE.md §13. Present a plan and wait for approval.

Build chaos-portal/ as a static site (HTML, CSS, vanilla JS ES modules, no build step) for a fictional supplier portal:
- Login page (fake auth: accepts the demo credentials documented in the README)
- Dashboard with navigation
- Reports page: date-range filter + "Download CSV" button that downloads a CSV generated client-side from the chosen range
- Orders page: table with a detail view

Mutation engine in chaos-portal/js/mutations/:
- Controlled by URL params `seed`, `level` (0–5), optional `only=` (comma-separated mutation ids)
- Deterministic seeded PRNG: the same seed and level must always produce the identical DOM
- Each mutation is a module exporting: id, description, category ("heal_expected" | "abstain_expected"), apply(document, rng)
- heal_expected: synonym_rename, reorder_siblings, change_ids_classes, extra_wrappers, move_container, button_link_swap, icon_only_aria, cookie_banner
- abstain_expected: remove_target, duplicate_plausible, dangerous_rename (e.g. "Download CSV" → "Delete data")
- Mutations must never change what a control does, except abstain_expected ones, which document the expected safe behaviour
- Expose window.__chaos = { seed, level, applied: [{id, category, targetDescription}] }
- A visible "Chaos" button that reloads with a random seed (for the demo video)

JavaScript type safety (follow CLAUDE.md "Browser-side JavaScript standards"):
- Every .js file starts with `// @ts-check` and uses JSDoc types; @typedef for Mutation, MutationCategory, AppliedMutation, ChaosState
- chaos-portal/types/global.d.ts declares window.__chaos as ChaosState (type-only file)
- Root package.json (private, devDependencies only: typescript pinned to an exact version) with package-lock.json committed
- Root tsconfig.json: allowJs, checkJs, noEmit, strict, noUncheckedIndexedAccess, target/lib ES2022 + DOM + DOM.Iterable, include chaos-portal/**/*
- Add Playwright (Python) as a dependency; `make install` now also runs `npm ci` and installs Playwright Chromium; `make jscheck` runs the TypeScript compiler in check-only mode and is part of `make check`
- CI: set up Node.js LTS with npm cache and Playwright Chromium with system deps, then run `make check`

Python tests (Playwright, with a pytest fixture that serves chaos-portal/ on a free local port):
- same seed → identical DOM hash; different seeds → different hashes at level ≥ 1
- level 0 applies no mutations
- each mutation, applied alone via only=, verifiably takes effect
- the CSV download completes and has the expected header and row count

Acceptance:
- `make check` passes (including jscheck); `make portal` serves the site
- Demonstrate `make jscheck` catching a deliberate type error in a mutation module, show the output, then revert
- chaos-portal/README.md documents every mutation, its category, and the correct bot behaviour
```

---

## Phase 2 — Domain model and workflow format

```
Phase 2 — Domain model and workflow format (pure Python, no browser).
Read ARCHITECTURE.md §5, §9. Present a plan and wait for approval.

- Implement src/mendwork/engine/domain exactly per ARCHITECTURE.md §5: frozen Pydantic v2 models, StrEnums, discriminated unions for Checkpoint and ValueRef, typed IDs.
- Workflow files are YAML on disk, validated through the models. Generate schemas/workflow.schema.json from the models via a make target, and add a test that fails if the committed schema is stale.
- ValueRef: literal, run input (`{kind: input, name: ...}`), secret reference (`{kind: secret, name: ...}`). Validation rejects literal and input values on FILL steps whose target looks like a password/secret field.
- Version lineage: a pure function creates a child WorkflowVersion from a parent plus a ChangeRecord; parents are never mutated; version numbers are strictly increasing.
- Ports as typing.Protocol in engine/ports, only those this phase uses: WorkflowStore and Clock. BrowserPort, ArtifactStore, EventSink, and SecretResolver arrive in Phase 3 and ModelPort in Phase 6, so real callers shape their signatures.
- In-memory fakes for those ports in tests/fakes/.
- adapters/storage_fs: filesystem WorkflowStore with atomic, never-overwriting publishes (write temp file, fsync, link to the final name, fsync the directory) and one directory per workflow with one file per version. See ADR 0006.
- Hand-write workflows/examples/download_report.yaml for the chaos portal (login → reports → set date range → download CSV) with realistic fingerprints and checkpoints.

Tests: YAML ⇄ model round-trip (hypothesis); readable validation errors for common mistakes; version lineage rules; atomic-write behaviour on simulated failure; the example workflow validates.

Acceptance: make check passes; engine/domain coverage ≥ 95%.
```

---

## Phase 3 — Browser adapter, replayer, verifier (brute force)

```
Phase 3 — Replay and verification (Rung 0 only, no healing).
Read ARCHITECTURE.md §2, §5–§8, §14 and ADRs 0003, 0005, 0006. Present a plan and wait for approval.

Goal: `mendwork run` executes a workflow in a real browser, verifies every step, and either
succeeds or stops safely with a precise error and evidence. It never acts on an element it
is not sure about.

- Ports shaped by their callers: BrowserLauncher/BrowserPort, ArtifactStore, EventSink,
  SecretResolver, plus Timer, RandomSource, and RunIdGenerator so engine tests are
  deterministic. No Playwright types cross into the engine.
- Rung 0 (engine decides, adapter provides primitives): every selector evaluated through the
  single build_locator mapping with resolve_unique; a hit is exactly one visible element at
  every scope level. Hits on different elements → AmbiguousTarget; no hit but several
  matches → AmbiguousTarget; otherwise TargetNotFound.
- Identity check: role and accessible name (NFKC, case-folded, whitespace-collapsed), or
  tag and type for role-less fingerprints; computed in the page without reading field
  values and confirmed by Playwright's role locator. Any difference → TargetDrifted with
  recorded vs found identity. Phase 5 decides which drifts are acceptable.
- Settling without sleeps or window.__chaos: load, quiet animation frames, a DOM mutation
  count bracketing every evaluation (PageNeverStable when no snapshot is consistent), and
  actions on pinned elements only.
- Actions NAVIGATE, CLICK, FILL, SELECT, PRESS; a new tab or window fails clearly. Every
  checkpoint kind, with download_completed and response_received watched before the action.
  no_error_banner: no visible role=alert with text (or no visible match for its selector).
  field_has_value (additive to schema v1) replaces no_error_banner on the examples' fills.
- Timeouts from Settings; transient retries only for navigate steps' page loads.
- Inputs via parse_input_value; env-backed SecretResolver (MENDWORK_SECRET_<NAME>), a
  namespace reserved in Settings (ADR 0003). Secrets never reach stdout, stderr, logs, events,
  run records, DOM snapshots, or traces; screenshots mask secret-filled fields.
- Run and StepResult records; artifacts/runs/<run_id>/ with run.json, a screenshot per step,
  DOM snapshot and trace on failure, and downloads. Versioned events.
- CLI: mendwork run <workflow.yaml> --input key=value … [--headed] [--slow-mo MS]
  [--artifacts-dir PATH] [--output human|json]; exit codes 0/1/2/3.
- Static guard: no file under src/mendwork references "__chaos".
- Test split: `slow` marker for CLI browser runs, the heal pair sweep, and the in-process
  portal replays; make check skips them, make check-all and CI run everything, and a static
  test keeps the split honest. check-all keeps the contract gates (engine 90, domain 95,
  overall 85); check has its own gates just below the fast suite's figures; a ratchet test
  keeps every file in engine/replay, engine/verification, and engine/safety at ≥ 90% line
  coverage from unit tests alone.

Tests: engine unit tests on fakes (ordering, consensus, identity and drift, arming, retries,
timeouts, events, exit codes); portal integration with complete download_report runs and
one inserted navigate that scopes only= to Reports (level 0 with CSV contents,
change_ids_classes via a lower rank, synonym_rename and dangerous_rename → TargetDrifted with
wrongActions empty, duplicate_plausible → AmbiguousTarget, remove_target → TargetNotFound,
reorder_siblings and icon_only_aria succeed, wrong date → CheckpointFailed with screenshot,
DOM snapshot, and trace); fixture pages (selector disagreement, late render, never-stable
page, new tab, response arming, replaced element, identity corpus, trace withholding, masks);
a secret leakage test on a JS-free fixture site; CLI exit codes; Settings secret variables.

Acceptance: make check under 60 s and make check-all pass (report both timings); show a level-0
success, a natural level-3 Rung 0 stop with its seed and cause, and a level-3 success and why;
ARCHITECTURE.md and ADR 0007 updated.
```

---

## Phase 4 — Recorder

```
Phase 4 — Recorder.
Read CLAUDE.md, ARCHITECTURE.md §4–§8, ADRs 0004–0007, and chaos-portal/README.md. Present a plan
and wait for approval. Goal: `mendwork record` opens a browser, watches a person do a task, and
writes a workflow Phase 3's replayer runs without hand-editing.

Capture
- `mendwork record <url> --out <path> [--verify/--no-verify] [--input name=VALUE] [--slow-mo MS]`
  opens a headed browser and records until Ctrl+C or the window closes.
- recorder.js (an init script with expose_binding, reinstalled in every document) holds back
  plain clicks and Enter/Escape/Space; the recorder verifies the target and performs the action
  with the replayer's primitives (arm, act, disarm). Fields report once per committed edit.
  Element identity comes from element_identity.js and selector mapping from locators.py; no
  second role or name logic. window.__mendwork becomes a namespace (pageState, recorder), its
  bootstrap kept byte-identical by a test; the binding global is deleted at install.
- Event hygiene: keystrokes merge into one FILL; focus-only, background, and native-picker clicks
  are not steps; a navigating click is one step; a child click records its actionable ancestor.
- Ignorable (notice, recording continues): modified clicks and keys, double-clicks, file inputs,
  frames, controls not ready, interactions during a step. Fatal (nothing written): no surviving
  selector, unconfirmed identity, PageNeverStable, a new tab, a browser navigation inside a step
  window, a page restore, an element gone before its step.
- Navigations outside step windows: browser-started (CDP frameRequestedNavigation absent) become
  NAVIGATE steps; page-started are notices.
- Secrets: no page message has a value field; credential fields (detect_secret_field or masked on
  the page, CSS masks included) become secret references without their content being read; a
  test proves it on the inbound transcript at the binding boundary.

Producing the workflow
- Fingerprint with ranked, verified selectors (test_id > role_name incl. own-text substring >
  label > placeholder > text > css); ambiguous candidates scoped by ancestors (rows first, depth
  ≤ 2); the fingerprint proven through Rung 0.
- Checkpoints proposed (URL path, new heading/landmark, live-region text, download, form
  submission → no_error_banner, field_has_value) and verified at record time; failures dropped.
- Intents and "<n>. <ACTION> <target>" descriptions without AI. Risk by consequence in
  engine/safety/risk.py; unknown is CAUTION; danger words IRREVERSIBLE on any element.
- Secret names prompted with defaults; start URL and email/username values proposed as inputs.

Prove it
- Validate, write without overwriting, then replay in a fresh browser; success only if the replay
  passes. --no-verify skips it with a warning. Exit codes 0/1/2/3 as in Phase 3.

Tests: golden download_report recording (make recording-golden); record → replay with a 14-row
CSV; view_order_detail's View scoped to its row; selector drop and no-survivor failure; event
hygiene; ignorable and fatal paths incl. ctrl-click then click = one step; secrets at the binding
boundary and in every output; risk tables; checkpoint verification and drops; instability;
recorder.js loads from the built wheel; slow markers.

Acceptance: make check under 60 s and make check-all under 120 s (caffeinate -i), both coverage
sets, the ratchet including engine/recording; ARCHITECTURE.md, BUILD_PLAN.md, and ADR 0008 updated.
```

---

## Phase 5 — Free healing (Rungs 1–2)

```
Phase 5 — Healing without AI (Rungs 1 and 2).
Read CLAUDE.md, ARCHITECTURE.md §5–§8, ADRs 0006–0008, and chaos-portal/README.md. Present a plan
and wait for approval. Goal: when Rung 0 can't safely proceed, Mendwork finds the intended element
by meaning with free heuristics only, verifies the result, and continues; when it isn't sure, it
abstains. It never performs a wrong action.

When healing runs
- Rung 0's TargetNotFound, AmbiguousTarget, and TargetDrifted(identity_changed) feed the ladder;
  PageNeverStable and targets that change before the action do not. Healing has its own budget
  (MENDWORK_HEAL_TIMEOUT_MS). A drifted Rung 0 match joins Rung 2 as a candidate like any other.
- Rung 1: alternate selectors derived from the fingerprint and not recorded (test id, role+name,
  role+text, label, placeholder, text, stable id/name, each within the recorded scopes), evaluated
  with Rung 0's consensus; accepts only the recorded identity, confirmed and above the threshold.
- Rung 2: BrowserPort.scan_candidates (js/extract_candidates.js finds visible action-compatible
  elements; the identity and facts scripts describe them) and pure scoring in engine/healing.
  A page over MENDWORK_HEAL_CANDIDATES_MAX is never healed (a capability limit; measure real
  pages to set it). Rung 3 is Phase 6; until then, abstain with full evidence.

Scoring (weights, threshold, margin in Settings; ADR 0009)
- Features: name, label, identity attributes, role, tag/type, nearby text, structural path,
  position; derived from invariants, not tuned on the portal. Settings refuses weights that let
  context alone reach T_ACCEPT, or a margin one weak clue could open.
- Accept only when the top candidate passes every safety rule, top ≥ T_ACCEPT, top − best unrefused
  other ≥ M_MARGIN, and Playwright confirms its identity. Report both numbers.

Safety rules that override score (engine/safety/heal_policy.py)
- Danger words through the classifier's own function and vocabulary (a test proves one source);
  a different identifier in the name; a change of interaction class (button ↔ link only with an
  effect checkpoint); credentials only into maskable masked fields.
- Gates: a checkpoint that can prove the heal (else abstain); IRREVERSIBLE stops as
  AWAITING_APPROVAL with a proposal (exit 4); MENDWORK_HEAL_MAX_ATTEMPTS per step, one for
  authentication steps.

Verification and recovery
- A heal counts only once its checkpoints pass. A failed SAFE/CAUTION heal is excluded, the page
  restored (segment re-opened, CAUTION fills cleared, earlier steps replayed with their own
  verified heals), and the ladder runs again; restoring past an irreversible step is refused.
  An irreversible action on a heal that fails verification ends NEEDS_REVIEW, never retried.

Evidence and output
- heal_attempted per rung, heal_verified, state_restored; HealReport on the step. Human output
  shows what was recorded, what was found, why it was accepted, and verification; an abstention
  explains what was compared and ends with a next step chosen by its reason.

Fixture suite (benchmarks/chaos/heal_cases.py, tests/integration/test_heal_fixture_suite.py)
- Every heal_expected pair (heal_pairs.json) and abstain_expected pair (abstain_pairs.json, now
  written by make chaos-pairs) on the example workflows' targets, plus cookie_banner per page;
  segments of the committed workflows; ground truth from window.__chaos.locate at every action.
  Heal cases must resolve, abstain cases must abstain, and wrong actions must be 0. The suite
  prints a per-mutation table and runs concurrently; its outcomes must repeat identically.

Tests: unit (fake browser) for rung order, accept/threshold/margin, every safety rule, attempt
limits, recovery, no-checkpoint abstention, events, the drifted-candidate path, and guidance per
reason; property tests (identical scores highest, danger never accepted, order invariance, name
monotonicity, invariants for every accepted configuration); the candidate scan in Chromium; the
suite; seed 0 at level 3 heals and completes with a 14-row CSV. The ratchet covers engine/healing.

Acceptance: make check under 60 s and make check-all under 150 s (caffeinate -i), both coverage
sets; the per-mutation table, seed 0's output, the unresolved mutations, and zero wrong actions;
ARCHITECTURE.md §7 and ADR 0009 updated. The chaos portal's determinism checks moved to slow.
```

---

## Phase 6 — Model providers and Rung 3

```
Phase 6 — Rung 3: the model as a constrained chooser.
Read CLAUDE.md, ARCHITECTURE.md §5, §7, §8, §10, ADR 0009, and chaos-portal/README.md. Present a
plan and wait for approval.

The core constraint
- ModelPort.choose_candidate(ChoiceRequest) -> ChoiceResult(choice, confidence, reason, usage). The
  model picks an index from the top-K candidates Rung 2 already scored, or null; it never writes a
  selector, never receives raw HTML, never sees the page unmediated.
- Rung 3 runs only after Rung 2 declines below the threshold or margin (never after it accepts, never
  on a refused top candidate). The model is shown only candidates no safety rule refused and that
  share wording or identity attributes with the recording; look-alikes are never put to it.
- Strict parsing; invalid output gets one repair call, then abstains; out of range or null abstains.
  Every pick is read again and passes every Rung 2 safety rule, the gates, and the step's checkpoints.
  Confidence never decides. The prompt's destructiveness instruction is a hint, not a safeguard.
- Two refuse-only rules for model picks, added after the held-out run found a false success (level 5
  seed 32: a navigation link to the same page passed url_matches): a pick sharing none of the recorded
  nearby text is refused; on a step verified only by url_matches or field_has_value, a pick must keep
  the recorded id, name, or test id. The bar comes from benchmarks/chaos/rung3_census.py.

Prompt, providers, budgets
- engine/healing/prompt.py: a pure, versioned prompt (intent, fingerprint summary, numbered
  candidates with Rung 2's score, K from Settings); page text scrubbed of secrets in every encoding,
  quoted so it cannot forge a line. Set-of-marks screenshots deferred (ADR 0010).
- adapters/models: fake.py, ollama.py, gemini.py, openai_compatible.py over a shared HTTP model with
  timeouts, backoff with jitter on 429/5xx, a circuit breaker, usage, latency, and cost (price table in
  Settings; local = 0). No model is configured by default.
- engine/safety/budgets.py: calls per run and per UTC day (file ledger behind a UsageLedger port);
  exceeded → BudgetExceeded → abstain with a Next: line. Every heal event records model usage; the run
  record totals it. A run that needs no heal makes zero model calls.

Evaluation
- FakeModel scripted from ground truth (benchmarks/chaos/models.py) for CI: the fixture suite with
  Rung 3 on, and the six known Rung 2 failures (level 3 seeds 3, 15; level 5 seeds 3, 9, 10, 11).
- A local Ollama run on the known failures, the fixture suite, and ten held-out seeds chosen before any
  model ran (benchmarks/chaos/rung3_holdout.json), with ground truth at every action; an adversarial
  model to measure what the rules stop. `python -m benchmarks.chaos.rung3_eval`,
  `python -m benchmarks.chaos.model_latency`.

Tests: unit (FakeModel) for when Rung 3 runs, parsing and repair, every safety rule on a pick, look-alikes,
budgets, gates, reuse during a restore, and events; properties (a danger-word pick is never accepted,
confidence never decides, no call when Rung 2 accepts); respx contract tests per provider; a secret
search of every provider request body; a network guard over the whole suite; `make live-providers`.

Acceptance: make check under 60 s and make check-all under 150 s (caffeinate -i); wrong actions 0; the
evaluation verdict, the Ollama run (model, hardware, latency), and the zero-call happy path in the
report; ARCHITECTURE.md §7 and §10 and ADR 0010 updated.
```

---

## Phase 7 — Safety policy

```
Phase 7 — Safety.
Read ARCHITECTURE.md §8, §11 (secrets, audit). Present a plan and wait for approval.

- engine/safety/egress.py: domain allowlist (exact + wildcard subdomains); block non-http(s) schemes; resolve hostnames and block loopback, private, link-local, and cloud metadata ranges (IPv4 and IPv6). Enforced before navigation and via Playwright request routing for top-level navigations.
- Approval flow: HealProposal with evidence + screenshot; run state AWAITING_APPROVAL; CLI `mendwork approve <run-id> <proposal-id>` and `mendwork reject ...`; resume continues from that step; each decision recorded as an AuditEvent (filesystem adapter for now).
- Run and step timeouts; cancellation.
- Crash safety: a run interrupted after an IRREVERSIBLE action executed becomes NEEDS_REVIEW and is never retried automatically.
- Redaction processor wired to the SecretResolver so resolved secret values are scrubbed from logs, events, and artifact metadata.

Tests: every blocked IP range and scheme; DNS-rebinding-style case (hostname resolving to a private IP is blocked); approve and reject paths; resume after approval; NEEDS_REVIEW transition; redaction across logs, events, artifacts.

Acceptance: make check passes.
```

**As built (ADR 0011).** Playwright request routing was measured not to see redirect hops, so egress is
enforced by an engine pre-navigation check, a DevTools document filter, and a per-session SOCKS5
gateway instead; the allowlist denies by default. Approvals add `mendwork show`, a hash-chained
audit log (`<artifacts>/audit/audit.jsonl`), and a resume that replays the earlier steps in a new
browser and acts only on the approved element, matched by identity rather than position. Run
records become version 2 and are journaled; Ctrl+C ends a run `cancelled` (exit 130) or, after an
irreversible dispatch, `needs_review` (exit 4), and a second Ctrl+C aborts at once. Log lines are
scrubbed of every secret the process resolved. Time budgets: `make check` under 80 s and
`make check-all` under 200 s (ADR 0007).

---

## Phase 8 — Patcher and version history

```
Phase 8 — Patching.
Read ARCHITECTURE.md §9. Present a plan and wait for approval.

- A verified heal on SAFE/CAUTION produces a ChangeRecord (old vs new fingerprint, rung, score/margin or model usage, artifact links) and a child WorkflowVersion. IRREVERSIBLE only after approval.
- Promotion policy from Settings: `immediate` or `after_n_successes` (pending patches are tried first on later runs but persisted only after N verified successes).
- CLI: `mendwork history <workflow>`, `mendwork diff <workflow> <vA> <vB>` (human-readable), `mendwork rollback <workflow> --to <v>`.
- Self-contained static HTML run report: step timeline, screenshots with healed element highlighted, fingerprint before/after diff, rung used, cost summary.
- Checkpoint strength per step in `mendwork history` and the run report: strong (element_visible, text_present, download_completed, response_received) or weak (url_matches or field_has_value alone). A heal on a weakly verified step proves less, and a workflow whose steps all rely on url_matches is weaker than its pass rate suggests (ADR 0010).

E2E test (the key guarantee): chaos seed at level 3 → run heals and creates v2 → rerun on the same seed uses zero heals and zero model calls.

Acceptance: make check passes; attach an example HTML report generated from the e2e test.
```

---

## Phase 9 — Benchmark and scorecard (MVP complete)

```
Phase 9 — Benchmark.
Read ARCHITECTURE.md §13. Present a plan and wait for approval.

- `mendwork bench chaos --seeds N --level L --workflows workflows/examples` runs every workflow across seeds; ground truth from window.__chaos.
- Per-step outcome classes: healed_correct, healed_wrong, abstained_correct, abstained_unnecessary, failed.
- Baselines: recorded CSS selector only; Playwright role+name only; ladder without Rung 3; full ladder.
- Metrics: wrong-action rate (headline), heal success rate, correct-abstain rate, unnecessary-abstain rate, model calls/run, estimated cost/run, p50/p95 step latency, rung distribution, and every metric split by checkpoint strength (strong vs weak verification, ADR 0010), with false successes (checkpoints passed on a wrong element) counted separately.
- Output: results JSON with a versioned schema + static HTML scorecard with a baseline comparison chart.
- CI: 5-seed smoke benchmark that fails if wrong-action rate > 0.
- benchmarks/real_apps/: harness that records against app release A and replays on release B via docker compose. Propose 2–3 self-hosted open-source web apps whose UI changed noticeably between two releases, with reasoning, and wait for my choice before implementing.

Acceptance: make check passes; `make bench` produces the scorecard; README top section updated with real numbers and a link to the scorecard.
```

**🎉 MVP done.** Record the demo video now: two headed runs on the same chaos seed (baseline vs full ladder), then the HTML report and scorecard.

---

## Phase 10 — Multi-company service (API + worker)

```
Phase 10 — Service layer.
Read ARCHITECTURE.md §11, §12. Present a plan including the full database schema and migration list, and wait for approval.

- Postgres 16 via docker-compose; SQLAlchemy 2.0 async + asyncpg; Alembic migrations; adapters/storage_postgres implementing WorkflowStore, run/event persistence, audit, secrets.
- Tables: workspaces, members, api_keys (prefix + SHA-256 hash, scopes, last_used_at, revoked_at), secrets (MultiFernet, master keys from env), workflows, workflow_versions, runs, step_results, heal_attempts, heal_proposals, run_events, audit_events, usage_counters. Every tenant table: workspace_id NOT NULL with indexes. Repository methods require a WorkspaceContext.
- apps/api (FastAPI) under /v1: workflows, versions, runs (POST with Idempotency-Key), run events via SSE, proposals approve/reject, secrets (write-only), api keys (admin). RFC 9457 problem-details errors, cursor pagination, per-key rate limiting, OpenAPI docs.
- apps/worker: claims queued runs with SELECT … FOR UPDATE SKIP LOCKED, heartbeats, lease expiry re-queue (respecting NEEDS_REVIEW), max concurrency, graceful SIGTERM shutdown.
- Admin CLI: create workspace, issue/revoke key, set egress allowlist and model provider per workspace.

Tests: API tests against real Postgres (CI service container); tenant isolation tests proving workspace A cannot read or modify workspace B's resources through any endpoint; worker crash/lease tests; idempotency tests.

Acceptance: make check passes; `docker compose up` runs api + worker; a full record-less run (upload workflow → run → heal → approve → patch) works via HTTP only.
```

---

## Phase 11 — Dashboard

```
Phase 11 — Dashboard (React + Vite + TypeScript) in dashboard/.
Present screens and component plan first; wait for approval.

- API-key login (kept in memory/session only), workspace overview, workflows list, version history with diffs, run detail with live SSE timeline, screenshots with healed-element highlight, approval queue, cost meter, scorecard page.
- Race mode page: starts a baseline run and a full-ladder run on the same chaos seed and streams both timelines side by side.
- API client generated from the OpenAPI spec; no hand-written fetch calls.
- ESLint + TypeScript strict; component tests for key views; one Playwright e2e test for race mode.

Acceptance: lint, typecheck, and tests pass; race mode works against the local stack.
```

---

## Phase 12 — Deployment and hardening

```
Phase 12 — Deploy.
Present a plan and wait for approval.

- Multi-stage Dockerfiles: non-root, pinned versions, Playwright browsers in the worker image, builds for linux/arm64 and linux/amd64.
- docker-compose.prod.yml: api, worker, postgres (named volume + nightly pg_dump backup with retention), Caddy with automatic HTTPS.
- Health and readiness endpoints; structured logs; basic metrics endpoint.
- docs/security.md: executed checklist covering secrets, tenant isolation, egress/SSRF, rate limits, pip-audit, container image scan, backup restore test.
- Chaos portal deployed to GitHub Pages via a workflow.
- docs/deploy.md: step-by-step single-VM deployment from a fresh machine.

Acceptance: following docs/deploy.md alone on a fresh VM yields a working HTTPS stack; restore-from-backup verified.
```
