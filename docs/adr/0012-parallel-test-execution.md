# 12. Parallel test execution

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

Phase 7 finished with `make check-all` at 211.5 s and 211.3 s on a quiet machine, over the 200 s
budget, and `make check` at 66 s, within its 80 s. ADR 0007 says what happens then: the work stops,
and parallel test execution is decided as its own change with its own ADR, never by cutting tests
or widening `slow`.

Running tests in parallel had to keep four things true:

- **Determinism.** Every test has the same outcome whichever worker runs it and in whatever order,
  including the heal fixture suite's per-case outcomes and its zero wrong actions.
- **Coverage.** Both gate sets hold, with the same measured figures as a serial run.
- **Isolation.** No two workers share a browser, a server, or any file a test writes.
- **One configuration.** CI runs exactly what developers run.

## Decision

### Four workers, each test file whole on one worker

- **Dependency.** `pytest-xdist` 3.8.0 in the dev group, with `execnet`.
- **Configuration.** `make check` and `make check-all` run pytest with
  `-n $(PYTEST_WORKERS) --dist loadfile`, where `PYTEST_WORKERS ?= 4`.
  - `PYTEST_WORKERS=0` runs serially, for debugging.
  - CI runs `make check-all`, so it uses the same configuration.
  - The setting lives in the Makefile, not in pyproject's `addopts`. The coverage ratchet runs
    pytest in a subprocess of its own; with `addopts` that subprocess would start four workers
    inside a worker, and so would every single-test run.
  - `tests/unit/test_parallel_config.py` guards all three points.

### Why four

Both series ran the full suite with coverage, on an 8-core (4 performance, 4 efficiency), 8 GB M2.
Chrome and VS Code were open, so the figures compare within a series, not with a quiet
measurement.

**Series 1**, run in order:

| Workers | Wall | Peak swap |
|---|---|---|
| serial | 207.0 s | 0 |
| 2 | 126.2 s | 0 |
| 4 | 103.4 s | 306 MB |
| 6 | 97.3 s | 1566 MB |
| 8 | 90.6 s | 1591 MB |

**Series 2**, both runs starting with about 1.4 GB already swapped:

| Workers | Wall | Peak swap |
|---|---|---|
| 8 | 105.8 s | 1910 MB |
| 4 | 106.8 s | 1870 MB |

**Fast suite:**

| Workers | Wall |
|---|---|
| serial | 61.6 s |
| 2 | 44.7 s |
| 4 | 38.1 s |
| 6 | 38.1 s |

- **8 workers won only on empty swap.** In series 1, eight workers beat four because the machine
  started with nothing swapped; they then pushed swap past 1.5 GB. Starting from the same swap,
  eight were no faster than four.
- **The fast suite stops at four.** Two more workers gained nothing.
- **Memory grows with the worker count.** Every worker runs its own Chromium, and the CLI tests
  start more.
- **CI has 4 vCPUs.** GitHub's `ubuntu-latest` runner for this public repository has four, so a fixed
  four is the same configuration everywhere. `-n auto` would mean eight workers locally and four
  in CI.

### Why `--dist loadfile`

- **Serial semantics.** Every test of a file runs on one worker, in file order, so module-scoped
  fixtures and in-file order behave exactly as in a serial run.
- **No repeated suites.** With per-test distribution (`--dist load`), the heal fixture suite's
  module fixture, which runs all 67 cases, would run again on every worker that received one of its
  tests, and the heal pair sweep and recording modules would repeat their setup.
- **The cost.** The longest file, `test_cli_approval_browser.py` (39 s serially), cannot be split. At
  four workers that is not the limit: about 200 s of test time finishing in about 105 s is CPU
  contention, not one long file.

### A session is one worker (amends ADR 0005)

Each worker is its own pytest process, so each gets its own session fixtures. No fixture code
changed:

- its own `PortalServer` on port 0, so the operating system gives each worker a different port;
- its own Playwright driver and Chromium;
- its own session event loop.

ADR 0005 is amended to say so, and ADR 0007 to measure its budgets with this configuration.

### Shared state, isolated per worker

| State | How it is isolated |
|---|---|
| Usage ledger | Under `tmp_path`, or under a temporary `--artifacts-dir`; per-worker default below |
| Artifact directories | Every command test passes a temporary `--artifacts-dir`. An autouse session fixture sets `MENDWORK_ARTIFACTS_DIR` to a directory under each worker's own base temporary directory, so a test that forgot the flag still cannot share or touch the repository's `artifacts/`. No test asserted the default, so none changed. |
| Audit log | Lives under the artifacts root, so it follows the row above |
| File locks (run claims, audit log, usage ledger, subprocess lock holders) | Every lock file is under a test's own temporary path; `flock` contends only on the same file |
| Portal and fixture-site servers | Port 0 |
| Coverage data | pytest-cov writes one data file per worker and combines them; the ratchet's subprocess uses its own data file and strips `COV_CORE_*` |
| Heal suite outcomes (`config.stash`) | Sent from the worker to the controller, below |
| Hypothesis example database | Disabled (`database=None`, derandomized) |
| Browser downloads and traces | Belong to each worker's own contexts |
| Wheel build | Writes to `tmp_path`; uv locks its own cache |
| structlog and root-logger state | Per process; reset after every test as before |
| SIGINT tests | Signal only their own child's process group |

### Tests changed to run in parallel

- **The heal fixture suite's outcomes cross from worker to controller.**
  - The problem: the suite runs in a worker, but the terminal summary (per-mutation table and
    wrong-action count) prints in the controller. Measured before this change, the table and the
    "wrong actions across the suite: 0" line silently disappeared under workers.
  - The worker side: the module fixture now calls `record_outcomes`, which also writes the outcomes
    as JSON into `config.workeroutput`.
  - The controller side: `pytest_testnodedown` rebuilds them. This hook, and
    `pytest_terminal_summary`, moved from `tests/integration/conftest.py` to `tests/conftest.py`:
    the controller collects no tests, so it never loads a conftest below the root.
  - Tests: `tests/unit/test_heal_reporting.py` round-trips the encoding as a hypothesis property and
    checks both hooks.
- **Shared process helpers.** The SIGINT tests' helpers moved to `tests/processes.py`, unchanged,
  so the shutdown tests below can use them.

## Finding: parallel execution surfaced a shutdown race that serial timing had hidden

Under four workers the machine ran with load 5–6 and swap near 3 GB. At that load, two parallel
`make check-all` runs out of six each failed one SIGINT test that had passed in every serial run.
The failures were a real product bug, not a flaky test.

**Cause.** The SIGINT tests send the signal to the CLI's whole process group, as a terminal's
Ctrl+C does. Playwright starts its driver as an ordinary child in that group, so the driver
receives the signal too and exits. When the machine is idle, Mendwork's teardown finishes first.
Under load, the driver is gone first, and Playwright reports every later call with a plain
`Exception("… Connection closed while reading from the driver")`, not its own `Error`.

**What went wrong,** in two places:

1. **The launcher's close** (`ChromiumLauncher` and `ChromiumRecordingLauncher`) called
   `Browser.close()` unguarded.
   - The run's record was correct (`cancelled`).
   - But the command exited 1 with a Playwright traceback instead of 130, which reads as a failed run.
2. **The session's teardown** (`open_session`: the document filter's detach, the trace stop, the
   context close; and the recording session's context close) tolerated only Playwright's own
   `Error`.
   - The plain exception replaced the interrupt, and the engine recorded an unexpected
     infrastructure failure.
   - In one run the record said `failed` (exit 3) although it listed an irreversible dispatch.
     ADR 0011 requires `needs_review` there, and a `failed` run invites a re-run that could repeat
     the action.

**Fix, in the adapter only.**

- `is_closed` also reads a plain `Exception`, with the same markers.
- One helper, `close_quietly`, runs each teardown step. It tolerates only an `is_closed` error,
  logs it at debug level (`chromium_already_closed`), and raises every other error.
- `close_chromium` closes the browser, then stops the driver, through it.
- Where it applies:
  - both launchers' close;
  - `open_session`'s teardown, which also serves approve/resume and record's verification replay;
  - the recording session's teardown.
- The engine is unchanged; ADR 0011's cancellation rules hold as written.

**Regression tests,** written first and seen failing on the unfixed code:

- `tests/integration/test_browser_shutdown.py` kills the test's own driver and waits for its exit
  event at a forced moment: just before the session is torn down, or just before the launcher
  closes. No sleeps and no retries. Its cases:
  - a finished run exits 0;
  - an interrupted run exits 130, with the driver gone before the launcher closes and before the
    session is torn down;
  - a run interrupted right after an irreversible click is recorded `needs_review` and exits 4;
  - a recording's browser closes cleanly in both orders.

  Every case asserts that the exit code equals the record's code and that no traceback appears.
  - Before the launcher fix: three of these cases failed.
  - With only the launcher fixed: the three teardown cases failed.
  - With the full fix: all pass.
- `tests/unit/test_chromium_close.py` covers the helper: closed errors tolerated and logged, and any
  other error from the close or the stop raised.

A serial suite runs each timing-sensitive test at the machine's lightest load, so it never shows a
race like this one. Running under real contention is part of why this change was worth making.

## Verification

This is the final series, on the code as committed here: the same machine, Chrome and VS Code open,
load 2.4–5.7, swap 2.6–3.3 GB.

| Target | Serial | Parallel (3 runs) |
|---|---|---|
| `make check` | 67.5 s | 48.1 s, 38.9 s, 37.6 s |
| `make check-all` | 216.6 s | 113.2 s, 123.1 s, 133.7 s |

- **Determinism.** Every parallel run matched the serial run test for test (JUnit XML): 1923 tests
  in the fast suite, 2182 in the full suite. The heal fixture suite's 67 per-case lines were identical
  in all three full runs and the serial run, with 0 wrong actions each time.
- **Coverage**, identical serial and parallel:

  | Gate set | Engine | Engine domain | Overall |
  |---|---|---|---|
  | Fast suite | 98.16 | 98.87 | 93.25 |
  | Full suite | 98.24 | 98.87 | 95.41 |

- **The reporting path catches a failure.**
  - The break: the case `synonym_rename-reports.download_csv` was temporarily mapped to the wrong
    target, and parallel `make check-all` ran.
  - The suite failed: exit 2; that case and the suite-wide wrong-action test failed.
  - The controller's summary printed "wrong actions across the suite: 1", the `synonym_rename`
    row showed one wrong case, and the per-case line read `wrong`.
  - The change was reverted.
- **Repeatability.** The SIGINT and egress browser tests passed three times under four workers
  (12/12 each).

## Alternatives considered

- **`-n auto`.** Eight workers here and four in CI: two configurations, and the local one swaps.
  Rejected.
- **Eight workers.** Measured faster only while swap was empty. Rejected.
- **`--dist load`.** Repeats module fixtures, the whole heal fixture suite included, on every
  worker. Rejected.
- **`--dist loadgroup`** with explicit groups for the modules that share fixtures. It could split
  long files, but needs another marker and a guard to keep groups complete, and no gain was measured
  at four workers. Not adopted; revisit if the long pole becomes the limit.
- **`-n` in pyproject's `addopts`.** Would parallelize the coverage ratchet's own subprocess and
  every single-test run. Rejected.
- **Signalling only the CLI process in the SIGINT tests.** Would have hidden the shutdown race
  instead of fixing it. Rejected.

## Consequences

- Both targets roughly halve their wall time on this machine, and the budgets (80 s and 200 s,
  unchanged) are measured with four workers.
- Every new test must pass on any worker in any file order:
  - write only under `tmp_path`;
  - bind servers to port 0;
  - keep no module state shared across files.

  CLAUDE.md says so.
- Memory use grows with workers: four Chromium processes plus the CLI tests' own.
- A debugging session can run serially with `PYTEST_WORKERS=0`; a serial run is not what the budgets
  measure.

### Limitations

- These figures come from a busy machine; the quiet-machine budget measurement is still to be taken.
- CI's parallel timings have not been measured yet.
- Four workers are fixed regardless of the machine; a larger runner does not use more.
