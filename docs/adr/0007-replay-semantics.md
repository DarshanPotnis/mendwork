# 7. Replay semantics: Rung 0, verification, secrets in artifacts, and output

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

Phase 3 makes `mendwork run` execute a workflow in a real browser. The run must either
succeed with every step verified or stop safely with a precise error and evidence. It must
never act on an element it is not sure about. Several decisions were not covered by
ARCHITECTURE.md or ADR 0006:

- what "the recorded selectors found the target" means when they disagree;
- how an element's role and accessible name are read without reading field values;
- how to wait for a page without sleeping, and never click a replaced element;
- which checkpoints must be watched before the action, and in what order they run;
- which failures are retried;
- how secrets are kept out of stdout, stderr, logs, events, run records, DOM snapshots,
  screenshots, and Playwright traces;
- which output goes to which channel, and what exit codes mean.

### Findings worth keeping

Measured with Playwright 1.62.0 and Chromium 151 while building this phase. Each one
changed the design, and each is easy to get wrong again.

1. **`aria_snapshot` exposes field values.** On a password input filled with
   `SUPER-SECRET-VALUE`, `locator.aria_snapshot()` returned
   `- textbox "Password": SUPER-SECRET-VALUE`. The same value came back with
   `aria_snapshot(depth=0)` and with `aria_snapshot(mode="ai")`
   (`- textbox "Password" [ref=e1]: SUPER-SECRET-VALUE`), and for a plain text input holding
   a secret. A `role=button` element containing an input returned that input's value inside
   its own name: `- button "Open INNER-VALUE"`. No mode keeps values out, so identity is
   never read through `aria_snapshot`.
2. **Resuming a trace too early leaks what was typed.** With tracing started
   (`snapshots=True, screenshots=True`), the chunk was discarded with `stop_chunk()` before
   a secret was typed into a password field. Calling `start_chunk()` while that same
   document was still loaded, then performing the next action (a navigation), produced a
   saved chunk whose `trace.trace` held the secret in a `frame-snapshot` entry: the next
   action's before-snapshot captured the old document, typed value included. The condition
   that fixed it: **call `start_chunk()` only once a different document is showing**,
   that is, after the navigation has loaded the new document and the page-state script
   reports a document token different from the one recorded when the secret was typed. The
   first snapshot of the resumed chunk is then of the new document; the saved chunk was
   measured clean. `TraceRecorder.observe_document` implements exactly this check.
3. **A headless `target=_blank` link can open no tab.** On a page served through a
   Playwright route, clicking such a link opened no page at all, while `window.open` did. A
   new-tab test must use `window.open`, or it passes without testing anything.

## Decision

### Consensus at Rung 0

- A selector **hits** when every scope level and the final locator each match exactly one
  *visible* element. Each level is searched inside the single visible element the level
  above found.
- Every selector in the ranked list is evaluated; nothing short-circuits on the first hit.
- Hits on **one** element: that element, found by the best-ranked hit. `target_resolved`
  records every selector's per-level counts and outcome, the winning rank, and the
  element identity.
- Hits on **different** elements: `AmbiguousTarget(reason=selectors_disagree)`, with the
  ranks that found each element and each element's identity. The engine never picks one.
- No hit, and some selector matched several elements: `AmbiguousTarget(reason=several_matches)`.
- No hit and nothing matched several: `TargetNotFound(reason=no_match)`.

### Identity, and drift as failure until Phase 5

- The found element is compared with the fingerprint:
  - with a recorded role, on role and accessible name;
  - without one (password and date inputs), on tag, effective type (HTML's default when
    the attribute is absent), and accessible name.
- Names are compared after NFKC normalization, case folding, and whitespace collapsing.
- **Any difference is a drifted match: `TargetDrifted`**, with recorded and found identity
  and the list of differences. Phase 3 never acts on one. Phase 5 decides which drifts are
  acceptable; an *unconfirmed* identity (below) must never be.
- Identity is computed by a page script (`element_identity.js`) that mirrors Playwright's
  role and accessible-name computation and **never reads a field's value**. A form control
  embedded in a name is reported, not read.
- Playwright then **confirms** it: a `role_name` selector with the computed role and exact
  name, built through the same `apply_selector` mapping Rung 0 uses, must find the element.
  If it does not, or a control is embedded in the name, the identity is unconfirmed, and
  that is a difference. Consistency with Playwright's matching is enforced at run time, not
  assumed. An element with no role or no name has nothing to confirm (`confirmed: null`).
- `aria_snapshot` was measured and rejected: it returns field values, passwords included,
  in every mode.

### Settling, stability, and stale elements

- Resolution waits for the load event and then for `settle_quiet_frames` animation frames
  without a DOM mutation (a `MutationObserver` count kept in one frozen global,
  `window.__mendwork`), bounded by `settle_timeout_ms`.
- The mutation count is read before and after every evaluation. A snapshot the DOM changed
  under is discarded and read again. Only a consistent snapshot decides.
- Ambiguous and drifted verdicts stop at once on a quiet page, and are retried until the
  step deadline on a busy one. Not-found always waits, for the next DOM change (never for
  time), until the deadline.
- A page whose DOM changes during every attempt fails with `PageNeverStable`: the page
  kept changing, so the target could not be verified safely. A page that only animates
  once per frame can still be read consistently between frames and is then resolved.
- The verified node is **pinned** as an element handle and every action goes through it,
  never through a selector that could match a different node a moment later. Immediately
  before the action its identity is read again: a changed identity is `TargetDrifted
  (reason=changed_before_action)`, a detached node `TargetNotFound
  (reason=detached_before_action)`. Playwright's hit-target check refuses a covered element.
- Nothing reads `window.__chaos`; a static test fails the build if any product file
  mentions it.

### Checkpoints and arming

- Order per step: resolve → pre-action checks (attached, visible, enabled or editable,
  action-compatible) → **watch** → action → settle → checkpoints → unwatch (always).
- The settle after the action (quiet frames, bounded by `settle_timeout_ms`) lets what the
  action caused (a new tab from `window.open`, a rendered banner, a navigation's load) arrive
  before anything checks for it. Browser events such as a new page are reported
  asynchronously, so checking the instant the click returns would race them.
- `download_completed` and `response_received` observe events the action causes. Their
  listeners are registered synchronously inside `watch`, before the action is dispatched,
  and queue what arrives, so nothing the action causes can be missed.
- Checkpoints run in the order written; `no_error_banner` runs last and once.
- `no_error_banner`: no visible `role=alert` element with non-whitespace text; with a
  selector, the selector matches no visible element.
- `field_has_value` (additive to schema 1, fill steps only): the field's value is compared
  inside the page with the step's resolved value, and only a boolean leaves the page. For
  a secret, only non-emptiness is checked.
- A new tab or window opened by the action fails the step with
  `NavigationError(reason=new_page_opened)`; multi-page flows are out of scope.

### Retries and timeouts

- Only a navigate step's page load is retried, and only for transient failures: timeouts,
  dropped or refused connections, `ERR_NAME_RESOLUTION_FAILED`, `ERR_NETWORK_CHANGED`, and
  HTTP 502/503/504. Certificate errors, NXDOMAIN, aborted loads, other statuses, clicks, and
  every resolution or checkpoint failure are never retried.
- Backoff: `min(max, initial · multiplier^(n−1)) · (1 − jitter · u)`, with `u` from the
  RandomSource port and pauses through the Timer port.
- A final main-document status of 400 or more fails the navigate step.
- Timeouts (Settings defaults): step 10 s, checkpoint 10 s (a checkpoint's `timeout_ms`
  overrides), navigation 30 s per attempt, settle 2 s, run 10 min. Every wait is capped by
  the run's remaining time; an `asyncio.timeout` around the steps is the backstop for a
  browser call that never returns. A timeout of 0 is never passed to Playwright.

### Secret handling in artifacts

- Workflows reference secrets by name. `EnvSecretResolver` reads
  `MENDWORK_SECRET_<UPPER_SNAKE_NAME>` at the moment of use; a missing or empty variable
  fails preflight (exit 2) before any browser starts. `.env` never supplies secrets
  (ADR 0003).
- Each resolved value is registered with the run's `SecretScrubber`, which removes it (raw,
  JSON-escaped, HTML-escaped, percent-encoded) from every piece of outside text entering a
  record: error messages and contexts, identities, checkpoint details, URLs, run inputs,
  and DOM snapshots.
- **Traces.** Traces record fill arguments and typed values in DOM snapshots, so:
  1. the current chunk is discarded *before* a secret is typed, and the document is marked
     tainted;
  2. recording resumes only once a *different* document is showing (resuming while the
     tainted document is loaded was measured to leak the value through the next action's
     snapshot);
  3. a failure on a tainted document keeps no trace, and the human output says: "trace
     withheld: the failing page still held a value typed at step N (step_id), so the trace
     could contain it. Re-run with --headed to watch the failure live.";
  4. a kept trace is scanned, member by member, for every secret in raw, JSON, HTML,
     percent-encoded, UTF-16, and base64 (all alignments) form, and deleted if anything
     matches.
- Screenshots mask every password input and every field a secret was typed into.
- Logs: engine code never logs values. Dev-mode tracebacks are plain, because the default
  renderer prints frame locals, where resolved secrets live.
- **Leakage test.** The chaos portal cannot be used: it ships its demo password in its own
  JavaScript, so a scan of anything it served could never be clean. The test uses a
  JS-free fixture site whose password field has no `name` (the form never sends it). Two
  CLI runs with a distinctive secret, failing after and during the secret page, are
  searched (stdout, stderr, every artifact, every member of every trace) in an encoding set
  the test derives itself; the search is shown to find a planted secret.

### Output channels and exit codes

- `--output human` (default): progress lines as events arrive, then a summary table, on
  stdout. `--output json`: one versioned event per line, then one final
  `{"result_version": 1, "exit_code": n, "run": …}` object, on stdout. Logs always go to
  stderr.
- Exit codes: 0 succeeded; 1 the run failed at a step (target, checkpoint, navigation,
  pre-action, run timeout, new tab); 2 invalid workflow, inputs, secrets, settings, or
  command line (nothing ran); 3 infrastructure (browser launch or crash, artifact store,
  an unexpected error, recorded in `run.json` before it is raised).
- Every started run writes `artifacts/runs/<run_id>/run.json`, a screenshot per step, the
  DOM snapshot and trace of a failed step, and any download.

### Test suite split and coverage gates

- `slow` marks the tests that launch Chromium through the CLI, the full heal pair sweep, and
  the in-process portal replays (`test_replay_portal.py`). `make check` runs everything else
  within its time budget (below); `make check-all` and CI run everything. A static test fails
  when a module uses a slow fixture, or the portal replay module loses its marker.
- **Time budgets (amended 2026-09-14):** `make check` under **80 s** and `make check-all`
  under **200 s**, measured in the foreground on a quiet machine: after a reboot to clear
  swap, with Chrome, VS Code, and other heavy applications closed.
  - *Why the figures changed.* The original target, a fast loop under a minute, was set in
    Phase 3, when the suite was a fraction of its current size. At the start of Phase 7 the
    fast suite had 1,545 tests. `make check` took 63.13 s in the foreground on a loaded
    machine (swap 7.2 of 8 GB, Chrome open). pytest took 59.29 s with branch coverage and
    49.81 s without; the coverage ratchet alone took 9.39 s.
  - *Baseline on a quiet machine* (Phase 6 tree, 2026-09-14): rebooted, Chrome and VS Code not
    running, load average 1.9. `make check` took 59.41 s and 58.38 s; `make check-all` took
    161.41 s and 161.07 s. That leaves about 21 s and 39 s of headroom. An earlier attempt 10
    minutes after a reboot measured `make check` at 67.78 s while Chrome was open (macOS reopens
    apps at login) and Spotlight was still indexing, so a measurement starts by confirming the
    machine is quiet, not by assuming it.
  - *What the budget protects.* The number was never the invariant. Two properties are:
    every test `make check` skips still runs in `make check-all` and CI, so the fast loop
    hides nothing CI would catch; and the coverage ratchet stays in `make check`, because it
    guards exactly the replay, verification, safety, recording, and healing code later phases
    add. Removing coverage, moving the ratchet out, or widening `slow` to meet a figure would
    trade those properties for a number, so none of them is an acceptable way to meet a budget.
  - *When a budget is exceeded,* the work stops and reports the measurements. Running tests in
    parallel (pytest-xdist) is the structural answer to a slow suite; if it is needed, it is
    decided as its own change with its own ADR, not inside a phase.
- **Two gate sets, and only one is the contract.**
  - `make check-all` (the full suite): engine ≥ 90%, domain ≥ 95%, overall ≥ 85%. **These
    are the coverage contract**, and CI enforces them.
  - `make check` (the fast suite): set just below what the fast suite measures, so a drop
    shows up locally before CI. They are an early warning, not the contract.
  - The Makefile labels every figure with the suite and gate that produced it.
- **A ratchet on the logic that decides.** A test in the fast suite runs the unit tests
  under coverage, in a separate process, and fails if any file in `engine/replay`,
  `engine/verification`, or `engine/safety` is below 90% line coverage. Rung 0, identity,
  consensus, checkpoint evaluation, and secret scrubbing must be proven by unit tests with
  the fake browser, never only by browser tests that happen to walk through them. The unit
  tests are a subset of the fast suite, so a file that passes the ratchet also passes under
  the fast suite.

## Alternatives considered

- **Trusting the first selector that hits.** Cheapest, but when selectors disagree it
  silently clicks whichever ranks higher. Rejected.
- **Identity from `aria_snapshot`.** Playwright's own computation with no page script, but
  it transmits field values, including passwords. Rejected (measured).
- **Identity from our script alone.** Transmits no values, but divergence from Playwright
  would be silent. Rejected in favour of script plus confirmation.
- **Waiting for a fixed quiet period.** Simple, but it is a sleep with extra steps and
  fails pages that never go quiet. Rejected for quiet frames plus the mutation-count bracket.
- **Acting through locators.** Playwright's recommended style, but a locator re-resolves
  at action time and can reach a node that was never verified. Rejected for pinned handles.
- **`expect_download` around the action.** Correct, but it pushes Playwright control flow
  into the engine. Rejected for synchronously registered listeners behind `watch`.
- **Retrying resolution.** Would mask the information a missing target carries. Rejected;
  resolution waits for the page instead.
- **Tracing off for any run that uses a secret.** Safe but throws away the trace of every
  failure after sign-in. Rejected for pause, resume on a new document, and scan.
- **Mutation scenarios from a pre-signed-in session on a reports-only workflow.** Faster,
  but skips the real sign-in steps. Rejected once measured: a single inserted navigate step
  scopes chaos to Reports while every committed step still runs.

## Consequences

- **Coverage gap in mutation scenarios.** Chaos reaches Reports through the inserted
  navigate step, not through the `open_reports` click, so those tests do not exercise a
  mutated page reached by a click. Natural level-3 runs (`benchmarks/chaos/rung0_seeds.py`)
  do.
- Pages that rewrite their DOM between every task cannot be resolved at Rung 0.
- After a single-page-app sign-in, the document never changes, so no trace is kept for
  the rest of that run.
- The accessible-name script covers the name sources workflows target (labels,
  `aria-labelledby`, `aria-label`, alt, title, placeholder, content with pseudo-elements).
  Anything it computes differently from Playwright is unconfirmed, which stops the step
  rather than acting.
- Shadow DOM and iframes are outside Rung 0's identity check in v1.
- A run interrupted by the operator (Ctrl+C) leaves `run.json` in `running`; cancellation
  arrives in Phase 7.
- The run-timeout backstop uses the event loop's real clock, so engine unit tests on the
  fake timer exercise the deadline path, not the backstop.
