# CLAUDE.md — Working rules for this repository

## What we are building

Mendwork is a self-healing browser automation engine. It records a workflow once, replays it deterministically for free, repairs broken steps with a cheap-first "heal ladder" (free heuristics first, AI only as a constrained chooser), verifies every repair, and saves verified repairs as new workflow versions.

`ARCHITECTURE.md` is the source of truth for design. `BUILD_PLAN.md` defines the current phase.

The quality bar is production-grade. It must be deployable for multiple companies, and no patchy work is acceptable.

---

## Before writing any code

1. Read the sections of `ARCHITECTURE.md` relevant to the current phase, and the phase itself in `BUILD_PLAN.md`.
2. **Present a plan first:** files to create or change, the responsibility of each, the tests you will write, and any new dependencies with a reason for each. **Wait for approval.**
3. Stay inside the current phase's scope. If something outside scope is required, stop and ask.
4. If a decision is not covered by `ARCHITECTURE.md`, propose 2–3 options with tradeoffs. After approval, record it as an ADR in `docs/adr/`.

---

## Architecture boundaries (non-negotiable)

- `mendwork.engine` may import only: the standard library, pydantic, rapidfuzz, structlog, and other `mendwork.engine` modules.
- `mendwork.engine` must **never** import `mendwork.adapters`, `mendwork.apps`, playwright, httpx, fastapi, sqlalchemy, or any vendor SDK.
- The engine talks to the outside world only through ports (`typing.Protocol` in `engine/ports`).
- `mendwork.adapters` implement ports and must never import `mendwork.apps`.
- `mendwork.apps` (CLI, API, worker) are composition roots: they wire adapters into the engine and contain **no business logic**.
- `import-linter` enforces these rules. Never weaken, disable, or bypass its contracts.

---

## Code standards

- Python 3.12+. Full type hints everywhere. `mypy --strict` must pass.
  - No `Any` except at a true boundary, with a comment explaining why.
  - `# type: ignore` only with a specific error code and a reason.
- Pydantic v2 at every boundary. Domain models are frozen. Use discriminated unions instead of free-form dicts.
- Async for all I/O.
  - Never `time.sleep`.
  - Never sleep to wait for page state; use Playwright auto-waiting and explicit conditions with timeouts.
- Small, single-purpose functions. Scoring, risk classification, and policy decisions are **pure functions** so they are trivially testable.
- Dependency injection through constructors. No module-level mutable state, no singletons, no hidden globals.
- Configuration only through `mendwork.settings.Settings` (pydantic-settings, prefix `MENDWORK_`). No hardcoded thresholds, weights, URLs, model names, or keys.
- Errors:
  - Raise types from `engine/errors.py`. Wrap third-party exceptions with context.
  - Never use bare `except`. Never swallow exceptions silently.
- Logging:
  - structlog with structured key-value fields; no `print`.
  - Always include `run_id` / `step_id` where available.
  - Never log secret values or the values of FILL steps.
- Time comes from the injected `Clock` port, never `datetime.now()` inside the engine.
- Public classes and functions get concise docstrings that explain *why*, not just *what*.
- Prefer adding new, well-named modules over growing existing files past roughly 300 lines.

---

## Browser-side JavaScript standards

JavaScript appears in exactly two places:
- the chaos portal (`chaos-portal/`)
- page scripts injected by the browser adapter (`src/mendwork/adapters/browser_playwright/js/`)

Both are plain JavaScript with **no build step**, type-checked by TypeScript.

- The first line of every `.js` file is `// @ts-check`.
- Types are written as JSDoc: `@param` and `@returns` on every function, `@typedef` for object shapes. Global or shared shapes go in type-only `.d.ts` files.
- The root `tsconfig.json` uses `allowJs`, `checkJs`, `noEmit`, `strict`, and `noUncheckedIndexedAccess`. Never loosen these settings.
- No `@type {any}` without a reason comment. `// @ts-expect-error` only with a reason.
- No runtime npm dependencies or frameworks. TypeScript is a dev-only dependency pinned to an exact version in the root `package.json`, with `package-lock.json` committed. Install with `npm ci`.
- Injected page scripts:
  - are fully self-contained (no imports), because they run inside arbitrary websites
  - expose at most one namespaced global (`window.__mendwork`) and never patch the page's globals or prototypes
  - never read or transmit the values of password or secret-like fields
  - remove any visual overlay they add before an action is performed
- JavaScript is never embedded in Python string literals. Page scripts live in `.js` files, ship as package data, and are loaded at runtime.

---

## Forbidden

- `TODO`/`FIXME` in committed code; placeholder bodies (`pass`, `...`, `raise NotImplementedError`) outside Protocol definitions, unless the phase plan explicitly allows it.
- Silencing linters, type checkers, or tests to get green; skipping or `xfail`-ing tests to pass a phase.
- `// @ts-ignore`, `// @ts-nocheck`, or loosening `tsconfig.json` strictness.
- Adding a dependency without stating why and getting approval.
- CAPTCHA solving, bot-detection evasion, fingerprint spoofing, or anything that circumvents a site's protections.
- Sending page content to a hosted model unless that provider is explicitly configured.
- Committing `.env` files, keys, real credentials, or real company data.
- Using real third-party websites in tests. Use the local chaos portal only.

---

## Testing rules

- Every behaviour change ships with tests **in the same change**.
- **Unit tests:** no network, no browser. Use the fakes in `tests/fakes/`.
- **Browser tests:** only against the locally served chaos portal or `page.set_content` fixtures.
- **Model provider tests:** recorded HTTP fixtures via `respx`. Live calls run only via `make live-providers`, never in CI.
- **Deterministic always:** fixed seeds, injected clock, no sleeps, no order-dependent tests.
- Tests that assert on CLI output must read it through the `plain_stdout` fixture (`tests/conftest.py`); rich/typer emit ANSI styling when `GITHUB_ACTIONS`, `FORCE_COLOR`, or `PY_COLORS` is set.
- Tests that launch Chromium through the CLI (the `cli_browser` fixture, the approval commands in `test_cli_approval_browser.py`, the interrupted `mendwork run` processes in `test_cli_interrupts.py`, and the driver-exits-first shutdown checks in `test_browser_shutdown.py`), sweep every heal pair, replay the examples against the portal in-process (`test_replay_portal.py`), run the heal fixture suite (`test_heal_fixture_suite.py`), check the chaos portal's determinism (`test_chaos_determinism.py`), prove the patching guarantee (`test_patching_guarantee.py`, and `test_cli_patching_browser.py` through the CLI), or record in Chromium (every `test_recording_*.py` browser module) are marked `slow`; `tests/unit/test_slow_marker.py` fails when the split drifts.
- `make check-all`'s coverage gates are the contract; `make check`'s are an early warning set just below the fast suite's figures (ADR 0007).
- **Parallel tests (ADR 0012):** `make check` and `make check-all` run pytest in four pytest-xdist workers with `--dist loadfile` (each test file whole on one worker), and CI runs the same targets. Every test must pass on any worker, in any file order: use `tmp_path` for every file a test writes, port 0 for every server, and never shared module state across files. `make check PYTEST_WORKERS=0` runs serially for debugging. Parallelism stays in the Makefile, never in pyproject's `addopts`.
- **Time budgets (ADR 0007, ADR 0012):** `make check` under 80 s and `make check-all` under 200 s, measured with the parallel configuration, in the foreground on a quiet machine (after a reboot to clear swap, with Chrome, VS Code, and other heavy apps closed). Every test `make check` skips still runs in `make check-all` and CI, and the coverage ratchet stays in `make check`. Work that exceeds a budget stops and reports the numbers; tests are never cut and `slow` is never widened to meet one.
- Every file in `engine/replay`, `engine/verification`, `engine/safety`, `engine/recording`, `engine/healing`, `engine/patching`, and `engine/reporting` must keep ≥ 90% line coverage from unit tests alone; `tests/unit/test_coverage_ratchet.py` enforces it.
- **Coverage gates:** `mendwork.engine` ≥ 90% lines; overall ≥ 85%.
- **A wrong click is a failing test.** The heal fixture suite's wrong-action count must be exactly 0.
- Use `hypothesis` for invariants: serialization round-trips, scoring monotonicity, policy ordering.

---

## Commands

| Command | Purpose |
|---|---|
| `make install` | Create env with uv, install deps, install pre-commit hooks; from Phase 1 also `npm ci` and Playwright Chromium |
| `make fmt` | ruff format + ruff check --fix |
| `make lint` | ruff format --check + ruff check (no fixes) |
| `make typecheck` | mypy --strict |
| `make imports` | import-linter contracts |
| `make jscheck` | TypeScript type-check of all browser-side JavaScript (from Phase 1) |
| `make test` | pytest in four workers with coverage gates, skipping tests marked `slow` (`PYTEST_WORKERS=0` for a serial run) |
| `make test-all` | pytest in four workers with coverage gates, including `slow` tests |
| `make check` | lint + typecheck + imports + jscheck + test (the fast local loop) |
| `make check-all` | lint + typecheck + imports + jscheck + test-all (what CI runs; must pass before any phase is done) |
| `make schema` | Regenerate `schemas/workflow.schema.json` from the domain models (a test fails when it is stale) |
| `make portal` | Serve the chaos portal locally |
| `make chaos-pairs` | Regenerate `benchmarks/chaos/heal_pairs.json` and `abstain_pairs.json`, the seed for every mutation–target pair |
| `make recording-golden` | Regenerate `tests/fixtures/recordings/download_report.yaml` from a fresh scripted recording (a test fails when it is stale) |
| `make bench` | Run the benchmark and build the scorecard (Phase 9+) |
| `make live-providers` | Opt-in live model provider tests (local only) |

---

## Definition of done (every phase and every task)

1. `make check-all` passes from a clean state.
2. New behaviour is tested and coverage gates hold.
3. If the design changed, `ARCHITECTURE.md` is updated and an ADR is added.
4. Nothing from the Forbidden list is present.
5. You give a short summary: what changed, why, how it was verified, and known limitations.
6. Commits use Conventional Commits (`feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`), one logical unit per commit.

---

## When stuck

Stop. Explain what is blocking, what you tried, and offer 2 options with tradeoffs. Do not guess, and do not hack around the problem to make it pass.
