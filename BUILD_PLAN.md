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
- ValueRef: literal, run input (`inputs.<name>`), secret reference (`secrets.<name>`). Validation rejects literal values on FILL steps whose target looks like a password/secret field.
- Version lineage: a pure function creates a child WorkflowVersion from a parent plus a ChangeRecord; parents are never mutated; version numbers are strictly increasing.
- Ports as typing.Protocol in engine/ports: BrowserPort, ModelPort, WorkflowStore, ArtifactStore, EventSink, SecretResolver, Clock — method signatures only.
- In-memory fakes for every port in tests/fakes/.
- adapters/storage_fs: filesystem WorkflowStore with atomic writes (write temp file, fsync, rename) and one directory per workflow with one file per version.
- Hand-write workflows/examples/download_report.yaml for the chaos portal (login → reports → set date range → download CSV) with realistic fingerprints and checkpoints.

Tests: YAML ⇄ model round-trip (hypothesis); readable validation errors for common mistakes; version lineage rules; atomic-write behaviour on simulated failure; the example workflow validates.

Acceptance: make check passes; engine/domain coverage ≥ 95%.
```

---

## Phase 3 — Browser adapter, replayer, verifier (brute force)

```
Phase 3 — Replay with exact selectors and verification. No healing yet.
Read ARCHITECTURE.md §6, §8 (checkpoints only). Present a plan and wait for approval.

- adapters/browser_playwright implementing BrowserPort with async Playwright: one BrowserContext per run, per-run downloads directory, configurable headless/headed, Playwright tracing saved on failure.
- engine/replay: executes a WorkflowVersion step by step using Rung 0 only (recorded selectors in ranked order). Exactly-one-visible-match rule: 0 → TargetNotFound, >1 → AmbiguousTarget.
- engine/verification: pre-action checks (visible, enabled, action-compatible) and evaluation of every Checkpoint kind, with per-checkpoint timeouts. No sleeps.
- Transient retry policy for navigation errors: bounded exponential backoff, separate from healing, configured via Settings.
- Run, StepResult records; events step_started / step_succeeded / step_failed / checkpoint_passed / checkpoint_failed via EventSink; adapter that writes JSON lines to stdout or a file.
- adapters/artifacts_local: screenshot after each step; trace + DOM snapshot on failure; paths recorded on StepResult.
- SystemClock and env-backed SecretResolver adapters.
- CLI: `mendwork run <workflow.yaml> --input key=value --headed --artifacts-dir PATH`, with a readable summary table at the end.

Tests:
- integration: chaos portal level 0 → run succeeds end to end, CSV downloaded and checkpoint passes
- integration: only=change_ids_classes → fails with TargetNotFound at the correct step, artifacts exist
- verifier unit tests using page.set_content fixtures for every checkpoint kind (pass and fail)
- secrets never appear in events or logs (assert on captured output)

Acceptance: make check passes; show the CLI output of both integration scenarios.
```

---

## Phase 4 — Recorder

```
Phase 4 — Recorder.
Read ARCHITECTURE.md §5, §8 (risk classification). Present a plan and wait for approval.

- `mendwork record <start-url> --out <workflow.yaml>` opens a headed browser and captures click, fill, select, press, and download events via an injected script plus expose_binding.
- The injected script lives in src/mendwork/adapters/browser_playwright/js/recorder.js, follows CLAUDE.md "Browser-side JavaScript standards" (self-contained, `// @ts-check`, JSDoc types, single namespaced global, never reads password values), ships as package data, is loaded at runtime, and is added to the tsconfig include so `make jscheck` covers it.
- For each target build a full Fingerprint and a ranked selector list (test_id > role_name > label > placeholder > text > css). Accessible role/name must be consistent with how Playwright's locators resolve them — verify each generated selector resolves to exactly one element at record time and drop those that don't.
- Merge consecutive keystrokes into a single FILL; ignore focus-only clicks; handle navigation between steps.
- Auto-propose checkpoints per step: URL change, newly visible heading/landmark, download event.
- Draft intent sentence from role + accessible name (no AI), e.g. "Click the 'Download CSV' button".
- Risk classification as a pure function (engine/safety/risk.py) using configurable danger keywords; stricter level when unsure.
- FILL on password/secret-like fields writes a secret reference, prompting for the secret name at the end of recording.

Tests:
- recording driven by scripted Playwright interactions (not manual) against chaos portal level 0 produces YAML matching a golden file (ignoring timestamps)
- the recorded workflow replays successfully with `mendwork run`
- risk classification table-driven tests, including tricky cases ("Submit feedback", "Remove filter")
- the recorder.js asset loads from an installed wheel, not just from the source tree

Acceptance: make check passes; demonstrate record → run green.
```

---

## Phase 5 — Free healing (Rungs 1–2)

```
Phase 5 — Healing without AI.
Read ARCHITECTURE.md §7 (Rungs 0–2), §8 (recovery). Present a plan and wait for approval.

- engine/healing/candidates.py: candidate extraction contract (what the browser adapter must return: role, name, label, text, attributes, nearby text, structural path, bbox) and filtering by action compatibility and visibility. The browser adapter implements extraction in js/extract_candidates.js (same JavaScript standards, covered by `make jscheck`); the engine only consumes the data.
- engine/healing/scoring.py: pure per-feature similarity functions and weighted total; weights, T_ACCEPT, M_MARGIN from Settings.
- engine/healing/ladder.py: Rung 0 → 1 → 2; accept only if score ≥ T_ACCEPT and margin ≥ M_MARGIN; otherwise abstain (Rung 3 comes next phase). Emit HealAttempt with top-5 candidates and per-feature breakdowns.
- Replayer integration: healed steps must pass verification; SAFE/CAUTION recovery tries the next candidate after restoring the last good checkpoint, up to MAX_HEAL_ATTEMPTS; IRREVERSIBLE steps with a healed target return AWAITING_APPROVAL (approval flow arrives in Phase 7 — for now the run stops in that state).
- benchmarks/fixtures/dom/: a script that generates before/after DOM snapshots from the chaos portal for every mutation and several seeds, with ground truth.
- Fixture test suite: heal_expected → correct element chosen; abstain_expected → abstain. A wrong choice fails the test.
- Property tests: an identical element always scores highest; for two candidates identical except name similarity, the closer name scores higher.

Acceptance: make check passes; print a results table per mutation (resolved / abstained / wrong) — wrong must be 0. Report which heal_expected mutations Rung 2 cannot yet resolve.
```

---

## Phase 6 — Model providers and Rung 3

```
Phase 6 — AI as a constrained chooser.
Read ARCHITECTURE.md §7 (Rung 3), §10. Present a plan and wait for approval.

- ModelPort.choose_candidate(ChoiceRequest) -> ChoiceResult(choice: int | None, confidence, reason, usage).
- engine/healing/prompt.py: builds the prompt from step intent, fingerprint summary, and top-K numbered candidates (K from Settings). Optional set-of-marks screenshot (numbered boxes over candidates) when the provider supports images; drawing happens in the browser adapter via js/set_of_marks.js (same JavaScript standards, covered by `make jscheck`), and the overlay is always removed before any action is performed.
- Strict parsing into Pydantic; invalid → one repair retry → abstain; out-of-range or null → abstain. Model confidence never accepts on its own; verification still decides.
- Adapters in adapters/models: fake.py (scripted), ollama.py, gemini.py, openai_compatible.py (base_url + key). Shared base: timeouts, exponential backoff with jitter on 429/5xx, circuit breaker, usage/latency/estimated-cost recording (cost table in Settings; local = 0). Model names come from Settings.
- engine/safety/budgets.py: max model calls per run and per day; exceeded → BudgetExceeded → abstain.
- Ladder: Rung 3 only after Rungs 0–2 fail or are ambiguous.
- Contract tests with recorded HTTP fixtures (respx) for each real adapter; `make live-providers` runs opt-in live calls locally.

Acceptance: make check passes; the fixture suite reports which cases Rung 3 resolved (using FakeModel scripted from ground truth for CI, plus a local run with Ollama documented in the phase summary); wrong-action count remains 0.
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

---

## Phase 8 — Patcher and version history

```
Phase 8 — Patching.
Read ARCHITECTURE.md §9. Present a plan and wait for approval.

- A verified heal on SAFE/CAUTION produces a ChangeRecord (old vs new fingerprint, rung, score/margin or model usage, artifact links) and a child WorkflowVersion. IRREVERSIBLE only after approval.
- Promotion policy from Settings: `immediate` or `after_n_successes` (pending patches are tried first on later runs but persisted only after N verified successes).
- CLI: `mendwork history <workflow>`, `mendwork diff <workflow> <vA> <vB>` (human-readable), `mendwork rollback <workflow> --to <v>`.
- Self-contained static HTML run report: step timeline, screenshots with healed element highlighted, fingerprint before/after diff, rung used, cost summary.

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
- Metrics: wrong-action rate (headline), heal success rate, correct-abstain rate, unnecessary-abstain rate, model calls/run, estimated cost/run, p50/p95 step latency, rung distribution.
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
