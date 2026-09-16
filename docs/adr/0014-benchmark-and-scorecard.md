# 14. Benchmark and scorecard

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

Phases 0 to 8 built a heal ladder that was measured, case by case, against the chaos portal's ground
truth: the heal fixture suite, seed surveys, and Rung 3's held-out evaluation. None of that says how
Mendwork compares with what people use today, and none of it is a number a reader can check. Phase 9
turns the work into published, defensible numbers.

The review set the terms:

- every outcome class defined precisely, above all what makes an abstention unnecessary;
- baselines run on identical seeds, without a mode in the product that could ship;
- a wrong action that passed its checkpoints (a false success) counted apart from one verification
  caught;
- a results document with a versioned schema, and a self-contained scorecard;
- a CI gate that fails when the wrong-action rate is above zero;
- a real application's release pair, with ground truth labelled by a person, or an honest account of
  why it was not done;
- every limitation of the measurement written down.

## Decision

### What is measured, and on what

- **The chaos grid.** Both committed example workflows (`download_report`, `view_order_detail`) at
  chaos levels 2, 3, and 5, on seeds 1000 to 1019. Level 2 is moderate change, level 3 harder, and
  level 5 everything, including one abstain-expected change per seed. The seeds were fixed in
  `benchmarks/chaos/bench_seeds.json` before any benchmark ran. Development used seeds 0 to 299 at
  level 3 and 20 to 119 at level 5, the pair tables use seeds up to 34, and tests use 424242, so no
  seed in the grid shaped a rule, weight, threshold, or prompt. A 30-seed, five-level grid was
  proposed, estimated at about 45 minutes from a measured 2.1 s of wall time per run at four runs at
  once; it was cut to this grid (decision D2) so that a fix can be re-measured in one sitting. The claim is scoped to exactly these levels and seeds, and the README
  says so.
- **Single mutations.** The heal fixture suite's 67 cases (ADR 0009) through every system: every
  heal-expected and abstain-expected change on a control the examples act on, and the cookie banner on
  each page, one change per page. Removed-control cases use a 1,500 ms step timeout, as the suite does.
- **A real application's release pair** (below).

### Ground truth, one step at a time

The engine never sees the portal. Ground truth reaches `mendwork.engine.benchmark` only as values:

- **`StepTruth`**: the step's control (`page.control`), whether acting there is correct (`act`) or any
  action is wrong (`abstain`: the control received `remove_target`, `duplicate_plausible`, or
  `dangerous_rename`), and the heal-expected changes its control received.
- **At every action**, the benchmark's browser wrapper asks `window.__chaos.locate` which of the
  workflow's controls the pinned element is, and notes how many run events had been emitted. Lined up
  with the events (`engine/benchmark/observe.py`), that says where the element came from (the latest
  `target_resolved` or resolved `heal_attempted`), whether the action was replaying an earlier step
  during a restore (between a failed `heal_verified` and `state_restored`, when it is checked against
  the replayed steps' controls), and what the checkpoints right after it said.
- **Around every action** it reads how many wrong actions the page recorded, so an activation no
  element check can see (Enter on a form) is attributed to its step.
- **When a run stops**, before the browser context closes, it reads whether the stopped step's real
  control was attached and visible.

The scripts (below) check the same things around their own locator.

### Outcome classes

Each reached targeted step gets exactly one class. The rules apply in this order and the first that
holds decides (`engine/benchmark/outcomes.py`):

| # | Class | Condition |
|---|---|---|
| 1 | `healed_wrong` | An action at the step reached an element that was not the step's control, any action happened at an abstain step, or the page recorded a wrong action while the step ran; and the first wrong element came from Rung 1, 2, or 3. |
| 1 | `direct_wrong` | The same, with the element from the recorded selectors (Rung 0) or a script's locator, or only the page noticed. |
| 2 | `approval_requested` | The step stopped for a person's approval. Its proposal is noted as naming the real control or not; it counts in neither abstention rate. |
| 3 | `healed_correct` | Expected act; the step succeeded; every action reached the real control; the element came from Rung 1, 2, or 3. |
| 3 | `direct_correct` | The same, from the recorded selectors or a script's locator. |
| 4 | `abstained_correct` | Expected abstain; the system decided not to act (below) and acted on nothing at the step. |
| 4 | `abstained_unnecessary` | Expected act; the system decided not to act and acted on nothing at the step, **while the real control was attached and visible when the step ended**. A correct action was available and was declined. |
| 5 | `ground_truth_unknown` | An action reached an element ground truth could not judge, and no action at the step was wrong. The benchmark did not see what was reached, so the step is counted neither wrong nor correct, and is reported on its own. |
| 6 | `failed` | Everything else: a correct action whose checkpoints failed; a stop that is not a decision (an unstable page, a failed navigation, a timeout, an infrastructure error); a decision stop on an act step whose control could not be confirmed there; any stop on an abstain step that was not a decision. |
| – | `not_reached` | The run ended before the step. Not in any rate. |

- **A decision not to act** is, for Mendwork, `HealAbstained` for any reason except
  `page_never_stable` and `heal_timed_out` (which report a page or a clock), `TargetNotFound`,
  `AmbiguousTarget`, `TargetDrifted`, and `BudgetExceeded`; for a script, a locator that found no
  element within the step timeout, or several (Playwright's strict mode). That is a plain script's
  only way not to act.
- **Wrong beats everything.** A step that acted on the wrong element and then recovered still acted on
  the wrong element.
- **Ground truth may decline to answer.** Every action is checked against ground truth at the moment it
  is sent, and ground truth reports one of three things: on target, off target, or unknown. The chaos
  portal always answers, so its cells never produce an unknown. A person's label on a real application
  is resolved on the live page, and a label that matches no element, or more than one, names nothing;
  an action checked against it is recorded as unknown. **An unknown is never counted as a wrong
  action** — the benchmark did not see what the action reached, and a measurement that cannot see must
  not accuse — and never as a correct one. It becomes `ground_truth_unknown`, which appears in the
  results, in the summary table, in the outcome stack, and in its own rate, so a suite whose ground
  truth stopped answering cannot be mistaken for a suite with nothing to report. Acting at all on an
  abstain step stays wrong whatever element was reached, because there the wrongness is in acting, not
  in the element.
- **Caught or false success.** A wrong step is a **false success** when the checkpoints checked right
  after a wrong action all passed (for a heal, that attempt's own `heal_verified`; for a direct action,
  the step's checkpoints); otherwise it is **caught**. This is the per-decision count ADR 0010
  recommended, not "a wrong action in a step that finally succeeded".
- **Two classes beyond the five the plan named.** `direct_correct` and `direct_wrong` exist because
  the script baselines never heal: without them a script clicking a renamed destructive control would
  have no class.

### Metrics

Every rate is stored and shown as `n of N`, because a system that stops early reaches fewer steps
(`engine/benchmark/metrics.py`):

| Metric | Numerator | Denominator |
|---|---|---|
| Wrong-action rate (headline) | wrong steps | reached steps |
| False successes, caught | wrong steps of each kind | – |
| Changed steps completed | correct steps | reached act steps whose control a release changed |
| Heal success | `healed_correct` | reached act steps where the ladder ran (0/0 for a script) |
| Correct abstentions | `abstained_correct` | reached abstain steps |
| Unnecessary abstentions | `abstained_unnecessary` | reached act steps |
| Model calls per run, estimated cost per run | run totals | runs |
| Step latency p50 / p95 | nearest rank over reached steps, split by recorded target, healed, and stopped | – |
| Resolutions, stop reasons | counts | – |
| Checkpoint strength | every metric above, again for strong, weak, and no verification (ADR 0010) | – |

- A call without a price is never shown as free: cost per run is unknown when any call was unpriced.
  The ground-truth chooser's calls have no price, because it is not a model.
- Changed-step completion is the one success rate comparable across all systems; heal success does
  not apply to a script.

### Systems, and why none of them is a mode of the product

| System | How it runs |
|---|---|
| `css_selector`: recorded CSS selector | `benchmarks/baselines/script_runner.py`: each step's recorded `css` selector through `page.locator`, then `click`, `fill`, `select_option`, or `press` with Playwright's auto-waiting and strict mode. Navigate is `page.goto`. No identity check, no consensus, no healing. |
| `role_name`: role + name locator | The same runner with the recorded `role_name` selector (through the product's own selector mapping, `within` scopes included), or the `label` selector for fields ARIA gives no role (password and date inputs), as Playwright's documentation advises. A step with neither is `unexpressible`. |
| `ladder_free`: Mendwork, free rungs | The product as shipped, `MENDWORK_MODEL_PROVIDER=none`, through the same wiring as `mendwork run`. |
| `ladder_ground_truth`: Mendwork + ground-truth chooser | The product with Rung 3 answered by the ground-truth chooser behind `FakeModel`, which answers only when exactly one listed line reads like the real control. It is not a model; its column is an upper bound on what Rung 3's rules allow. |
| `ladder_model`: Mendwork + a local model | The product with the configured model (`qwen3:4b-instruct-2507-q4_K_M` on Ollama), run one cell at a time so a queued call never spends the heal budget waiting. |

- **Not through the replayer.** Filtering a workflow to its CSS selector and replaying it with healing
  switched off would still run Rung 0's identity check and consensus, which refuse a control that kept
  its id but changed its name. The baseline would look safer than a plain script is. And no setting
  switches healing off; adding one would be exactly a mode that could ship.
- **What the scripts share: verification.** After each action they check the workflow's own
  checkpoints through the engine's `evaluate_checkpoint`, on the product's `PlaywrightSession`, with
  the download watch set before the action and the filled element pinned through
  `PlaywrightSession.pin_handle`, a harness-only adapter method beside `pinned_handle`. The comparison
  therefore isolates how a target is found.
- **The cost of that choice.** A plain script usually has fewer assertions and carries on after a
  wrong click, making more. The scripts' wrong-action counts are lower bounds.
- **No patching.** No system saves heals, so every run heals from the recorded version: the benchmark
  measures healing. Patching's zero-call guarantee is proven by its own tests (ADR 0013).

### The results document

`BenchmarkResults` (`engine/benchmark/results.py`, `schemas/bench-results.schema.json`,
`schema_version: 1`):

- **Provenance:** when, Mendwork's version, platform, Python, Chromium, concurrency, step timeout,
  levels, seeds, workflows, SHA-256 digests of `src/mendwork`, `chaos-portal`, `benchmarks`, and each
  workflow file, every setting that differs from its default (secrets excluded), and each model's
  provider, name, and prompt version.
- **Systems**, each described as the scorecard names it, with whether it is gated.
- **Sections**, each with its cells, every system's summary, and an **outcomes digest**: SHA-256 over
  each cell's identity, run status, model call count, and classified steps, in a fixed order, and
  nothing measured (durations, tokens, latency, cost). Reading a document recomputes every digest, so a
  results file edited by hand or cut short is refused.
- **Skipped sections**, with the reason.

`adapters/benchmark_fs` writes the document atomically (temporary file, fsync, rename, directory
fsync) and reads it back through the schema.

### The scorecard

`engine/reporting/scorecard_view.py` decides every figure and sentence from results documents alone;
`adapters/scorecard_html` lays them out.

- **It loads nothing and runs nothing.** Inline CSS, inline SVG charts, the reader's own fonts, no
  script, no link, no image, and `default-src 'none'; style-src 'unsafe-inline'; base-uri 'none';
  form-action 'none'`. Unit tests search the document for scripts, sources, links, `url(`, and
  `@import`; the benchmark's own render check loaded it in Chromium at three sizes and saw no request.
- **Escaped by default.** Every text goes through the run report's markup helper. SVG needs
  `viewBox`, so attribute names may now be camel-cased; tag names stay lowercase, and no name can hold
  a quote or a space.
- **Charts.** One small chart per metric, a row per system, Mendwork in the accent and baselines in
  gray, values at each bar's end; and one stacked bar per system splitting reached steps into outcome
  groups. The outcome palette (blue, aqua, yellow, magenta, violet, red) was validated for neighbouring
  segments in both themes: worst colour-vision separation 9.1 (light) and 8.4 (dark), normal vision
  19.6 and 19.3, all above their floors. Three light-mode hues sit below 3:1 against the page, so every
  chart has a legend and a table with its numbers.
- **Hover.** A page without script can offer only the browser's own tooltip: every mark is focusable
  and carries a `<title>`, and every value is also in a table.
- **Order.** Documents are shown in the order given, so a real application's pair leads when there is
  one.

### Commands

- `mendwork bench chaos --workflows DIR --level L … --seeds N [--seed-start S] [--system …]
  [--single-mutations] [--include RESULTS] [--gate] --output DIR` writes `chaos-results.json` and
  `scorecard.html`; `--gate` exits 1 when a gated system acted on a wrong element. `mendwork bench
  scorecard --results … --output FILE` renders any results files.
- **Where the harness lives (decision D1).** The harness reads `window.__chaos`, so it stays in
  `benchmarks/`, outside the product; a source guard already fails any product file that names it.
  The `mendwork` console script could not import it, because an editable install put only `src/` on
  the path. Hatch's `dev-mode-dirs = ["src", "."]` adds the repository root for editable installs
  only; the wheel still ships only `src/mendwork`, and the wheel test now proves it holds no
  `benchmarks/` or `tests/` file and no `__chaos`. From an installed wheel, `mendwork bench chaos`
  says it runs from a source checkout and exits 2.
- **A fifth import contract:** neither the engine nor the adapters import `benchmarks`.
- `mendwork bench real-app --output DIR [--system …] [--gate]` replays the recorded workflow on the
  later release of the pair, each system on its own freshly seeded container, and writes
  `real-app-results.json`. It refuses to score labels no person approved, labels that changed since the
  approval, and any setting other than the product's defaults.
- **`make bench`** runs the grid, the single-mutation cases, and every system with `--gate`;
  `BENCH_MODEL=1` adds the configured model's column; a real-app results file, when present, is shown
  after the grid in its own section, since the grid carries the broader claim and supplies the headline
  tiles. A unit test keeps the Makefile's levels and seeds equal to `bench_seeds.json`.

### The CI smoke gate (decision D3)

`tests/integration/test_bench_smoke.py`, marked `slow`, runs inside `make check-all`, so CI runs it
with no workflow change:

- **What.** Level 5, seeds 1000 to 1004, both example workflows, the free ladder and the ground-truth
  chooser: 20 runs, every action checked against ground truth, at a 1,500 ms step timeout.
- **The gate.** No wrong step and no false success in either system.
- **Reproducibility.** The same 20 cells in the committed `benchmarks/results/chaos-results.json` ran
  at the default 10 s step timeout. Their outcomes digest must match cell for cell. One comparison
  proves that the short timeout changes no outcome, that the seeds reproduce, and that the published
  numbers were made by the code under test. The cost: a change that alters these outcomes fails CI
  until `make bench` is run again, like `make chaos-pairs` and `make recording-golden`.
- **Why this subset.** Level 5 holds every kind of change, abstain cases included; the two gated
  systems are what ships; the scripts are expected to act wrongly, so gating them means nothing; single
  changes are already covered by the heal fixture suite; and the published grid's first seeds make the
  digest comparison possible.
- **Why 1,500 ms.** A stopped step spends its time waiting out Rung 0's patient not-found wait. Measured
  before this phase on level 5 seeds 0 to 4: 20.9 s at the default and 5.8 s at 1,500 ms, with
  identical outcomes; the digest comparison now proves the equivalence on every CI run.

### Real-application pairs

- **Candidates** (source at both tags, compared before the choice): Gitea 1.19.4 → 1.22.6 (227 English
  strings changed, 182 removed, 480 added; control labels kept; `head_navbar.tmpl`, `signin_inner.tmpl`,
  `new_form.tmpl`, and `repo/issue/list.tmpl` rewritten, `gt-*` classes replaced by `tw-*`); Linkding
  1.42.0 → 1.46.0 (Svelte to Lit, modal confirmations, tag forms in dialogs, layout templates moved);
  Memos 0.25.3 → 0.30.0 (sign-in form rewritten, editor rebuilt). **Gitea was chosen**: server-rendered,
  deterministic pages; setup without a wizard; a change that keeps wording and rewrites structure, which
  is where selector scripts break and meaning-based healing should hold.
- **Terms of service.** These are our own local instances of MIT-licensed software, run in containers
  on this machine. No third party's service is touched, so no site's terms apply.
- **Ground truth is a person's, and cannot be tuned to.** The workflow is recorded on release A with the
  recorder, from a scripted session. Before Mendwork runs on release B, every step of B is labelled:
  act, with a Playwright locator written for B from B's running UI and templates, or abstain, with a
  reason; never from Mendwork's output. A labels walk performs the workflow on B using only the labels,
  proving each matches exactly one visible element and saving a screenshot per step, and the user
  approves each label against its screenshot. The approval freezes the labels' SHA-256 in
  `benchmarks/real_apps/gitea/labels-<release>.approval.json`; scoring refuses to run without it, if
  the labels changed since it was given, or if the labels are for another release or workflow. Real-app runs refuse
  any setting other than the defaults and the loopback exception, and record the settings, workflow,
  labels, and image digests. Any engine change after labelling discards the release B results.
- **Images** are pinned by digest: `gitea/gitea:1.19.4@sha256:bca3994c102089aeb8d6bacd30d2b4f7056853fe9fb8d709bc81a103079b4f60`
  and `gitea/gitea:1.22.6@sha256:538658de667c5d098a274f2f63aa6ec891d88f670cdd5282cf27221ba747dda4`.

## Results

Measured on 2026-09-15 on the 8 GB Apple M2, four cells at a time, at the default 10 s step timeout.
The published run executed the code whose digests the results file records (`src/mendwork`
`sha256:8efdc1717fd…`, `chaos-portal` `sha256:0a2d804b5ba…`, `benchmarks` `sha256:f80e332e6f0…`).
`make bench` took 849.9 s.

### The chaos grid

Levels 2, 3, and 5; seeds 1000 to 1019; both example workflows; 120 runs and 780 targeted steps per
system.

| System | Runs succeeded | Steps reached | Wrong-action steps (false successes) | Changed steps completed | Heal success | Correct abstentions | Unnecessary abstentions | Model calls per run | Step latency p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|
| Recorded CSS selector script | 37 | 393 | 3 (0) | 114 of 194 (58.8%) | – | 0 of 3 | 80 of 390 (20.5%) | 0 | 71 ms / 10,003 ms |
| Role + name script | 31 | 414 | 0 (0) | 140 of 228 (61.4%) | – | 1 of 1 | 88 of 413 (21.3%) | 0 | 70 ms / 10,002 ms |
| Mendwork, free rungs | 77 | 619 | **0 (0)** | 262 of 293 (89.4%) | 74 of 105 (70.5%) | 12 of 12 | 31 of 607 (5.1%) | 0 | 142 ms / 10,067 ms |
| Mendwork + ground-truth chooser (upper bound) | 86 | 694 | **0 (0)** | 308 of 324 (95.1%) | 94 of 110 (85.5%) | 18 of 18 | 16 of 676 (2.4%) | 0.25 | 138 ms / 10,083 ms |

By level, changed steps completed and unnecessary abstentions:

| System | Level 2 | Level 3 | Level 5 |
|---|---|---|---|
| Recorded CSS selector script | 38 of 58; 20 of 170 | 46 of 72; 26 of 139 | 30 of 64; 34 of 81; 3 wrong-action steps |
| Role + name script | 49 of 68; 19 of 187 | 54 of 85; 31 of 141 | 37 of 75; 38 of 85 |
| Mendwork, free rungs | 68 of 75; 7 of 234 | 105 of 113; 8 of 234 | 89 of 105; 16 of 139 |
| Mendwork + ground-truth chooser | 71 of 76; 5 of 245 | 111 of 117; 6 of 245 | 126 of 131; 5 of 186 |

By checkpoint strength: every reached step had a checkpoint, and weak ones dominate (the free ladder
reached 498 weakly verified steps and 121 strongly verified). No wrong action by any system passed a
checkpoint. Mendwork's free ladder completed 44 of 45 changed strongly verified steps and 218 of 248
weakly verified; with the ground-truth chooser, 48 of 49 and 260 of 275.

### The Gitea pair: a release removed the evidence, not just the control

The workflow was recorded on Gitea 1.19.4 and replayed on 1.22.6: sign in, open a repository, open its
issues, and create one. Eight acting steps, labelled on 1.22.6 by the repository owner before Mendwork
ran on that release, approved against a nine-screenshot walk that performed the task with the labels
alone. Each system ran against its own freshly started, freshly seeded container, because the last step
creates an issue and no later run can undo it.

**Every system stopped at the same step, and the reason is not how it found the element.** Step 4 opens
the repository from the dashboard. Its checkpoints were recorded on 1.19.4: the URL matches
`/bench/demo`, **and** a heading whose accessible name is `bench / demo RSS Feed` is visible. Gitea 1.22
rewrote the repository header and that heading no longer exists — measured on both releases, the
headings on that page are `bench / demo`, `README.md`, `demo` on 1.19.4 and `README.md`, `demo` on
1.22.6. The step therefore cannot be verified on release B however the element is found: a system that
clicks exactly the right link fails the same checkpoint as one that clicks the wrong one.

That is a failure mode the chaos portal cannot produce. The portal scrambles the controls a step acts
on, but it never deletes the evidence that the step worked, so every chaos cell can still tell a good
heal from a bad one. A real upgrade removed the evidence itself. This is the most interesting thing the
pair found, and it is why the pair leads with it rather than with a rate.

| System | Steps reached | Wrong-action steps | False successes | Stopped at step 4 with |
|---|---|---|---|---|
| Recorded CSS selector script | 3 of 8 | 0 of 3 | 0 | *(never reached it)* |
| Role + name script | 4 of 8 | 0 of 4 | 0 | Playwright strict mode: two links match |
| Mendwork, free rungs | 4 of 8 | 0 of 4 | 0 | Rung 2 below margin |
| Mendwork + ground-truth chooser | 4 of 8 | 1 of 4 (caught) | 0 | attempts exhausted |

- **No system reached the irreversible step.** Creating the issue is the only irreversible step in the
  whole benchmark and the reason Gitea was chosen, so `approval_requested` went unexercised on a real
  application. The chaos suites exercise it; the pair does not.
- **The one wrong action, in full.** The ground-truth chooser clicked the labelled link
  `<a href="/bench/demo">bench/demo</a>` first; that click was on target. The checkpoint above failed,
  state was restored, and on the retry Rung 2 resolved the dashboard's other link to the same
  repository, `<a class="repo-list-link muted" href="/bench/demo">`, and clicked it. The label resolved
  to exactly one visible element at both moments, so ground truth was available and this is a real
  wrong action by the rule below, not an unknown. It was caught, nothing proceeded on it, and both
  links lead to the same page: a wrong element, not a wrong destination.
- **The duplicate link, decided before scoring.** Gitea 1.22's dashboard shows two links to the same
  repository with the same accessible name: one in the activity feed, one in the sidebar repository
  list. They are indistinguishable by role and name. The labelled control is the feed link, the one the
  recording acted on; abstaining there counts as an unnecessary abstention, and clicking the sidebar
  link counts as a wrong action. That rule was fixed with the labels, before any system ran.
- **A risk classification the recorder got wrong.** The recorder classified "Create Issue" as
  `caution`. Creating an issue cannot be undone, so the repository owner raised it to `irreversible`
  using the documented operator override. Risk is classified by consequence, and the recorder cannot
  always see the consequence.
- **The CSS baseline could not express five of the eight steps.** Our recorder emits a CSS selector only
  for an element with an id, and Gitea's buttons and links have none, so that baseline has no locator
  for them and stops at step 3 with `unexpressible`. That is a property of our recorder, not of CSS
  selectors in general: a person writing such a script by hand would use class or structural selectors.
  The number is reported as a limit of the baseline, never as a failure to find the element.
- **Not re-recorded to get a better number.** A checkpoint chosen now would be chosen knowing what
  release B removed, which is tuning the workflow to the release. The pair is published as it ran.

### Single mutations

The heal fixture suite's 67 cases, 140 targeted steps per system:

| System | Wrong-action steps (false successes) | Changed steps completed | Heal success | Correct abstentions | Unnecessary abstentions |
|---|---|---|---|---|---|
| Recorded CSS selector script | 5 (0) | 38 of 48 | – | 10 of 15 | 10 of 125 |
| Role + name script | 0 (0) | 34 of 48 | – | 15 of 15 | 14 of 125 |
| Mendwork, free rungs | **0 (0)** | 48 of 48 | 14 of 14 | 15 of 15 | 0 of 125 |
| Mendwork + ground-truth chooser | **0 (0)** | 48 of 48 | 14 of 14 | 15 of 15 | 0 of 125 |

### What the numbers say, and what they cost

- **No Mendwork system acted on a wrong element** in 1,593 reached steps. The CSS script acted wrongly
  8 times, each on a control renamed to a destructive action that kept its id (`dangerous_rename`), and
  each was caught by the step's checkpoint; an unasserted script would have carried on.
- **The role + name script never acted wrongly, and paid for it:** it declined 21% of the grid's act
  steps whose control was still there, every one a renamed control or a button that became a link.
- **Every unnecessary Mendwork abstention has a named cause:**
  - free rungs, 31: 29 `below_threshold` and 2 `top_rejected`. Of the 29, 26 are controls whose
    wording and ids a release changed together (14 on the email field, 12 on the sign-in button),
    which ADR 0009's invariant B keeps below the threshold by design and leaves to Rung 3. The other 3
    kept their ids but were renamed and also rewrapped and reordered (2, the email field) or moved
    away from their nearby text (1, the download button). The 2 `top_rejected` are "Orders" renamed
    "Purchase orders": the danger vocabulary's "purchase" refused the real link (ADR 0009's known
    limitation: danger detection is a vocabulary);
  - with the ground-truth chooser, 16: 3 `top_rejected` on the same rename, and 13
    `model_choice_refused`, where Rung 3 chose the real control and a rule for model picks refused it:
    12 on a renamed sign-in button whose ids also changed, on a weakly verified step, and 1 on a moved
    download button (the costs ADR 0010 predicted for its weak-verification bar and context veto).
- **Rung 1 healed nothing**, as ADR 0009 found. Every free heal came from Rung 2; the ground-truth
  chooser added 16 at Rung 3.
- **Latency.** Every system's p95 is about 10 s: a step whose control is gone waits out the step timeout
  before stopping, for a script's locator as for Rung 0. The median healed step took 299 ms (free
  rungs).

## Alternatives considered

- **Baselines through the replayer with healing switched off.** Less code, but Rung 0's identity check
  and consensus would still refuse a control that kept its id and changed its meaning, so the baseline
  would look safer than a script is; and the switch would be a mode that could ship. Rejected for a
  separate script runner in `benchmarks/`.
- **Baselines with no checkpoints**, like an unasserted script. They would count more wrong actions,
  but the comparison would then mix how a target is found with how a step is verified. Rejected; the
  scripts' wrong actions are reported as a lower bound instead.
- **Heal success as the rate every system is compared on.** It is undefined for a script, which never
  heals. Rejected for changed-step completion, which every system has.
- **Classifying whole runs instead of steps.** Simpler, but a run that stops at its first step hides
  everything after it, and one wrong click could not be told from ten. Rejected.
- **Charts from a JavaScript library, with scripted tooltips.** Richer interaction, but the page would
  load or run code, which the run report's self-containment rule and its Content-Security-Policy
  refuse. Rejected for inline SVG, native titles, and a table behind every chart.
- **The harness inside `src/mendwork`**, so the console script imports it. It would ship code that reads
  the portal's ground truth. Rejected (D1, option c).
- **`python -m benchmarks` instead of `mendwork bench`.** No packaging change, but not the command the
  plan specified. Rejected (D1, option b) for `dev-mode-dirs`.
- **The smoke gate at the default step timeout.** About four times slower (20.9 s against 5.8 s for ten
  runs, measured). Rejected for 1,500 ms, with the committed-digest comparison proving it changes no
  outcome.
- **The smoke gate without the committed-digest comparison.** Nothing to re-run after a change, but the
  published numbers could go stale without any test noticing. Rejected (D3).
- **Five levels and 30 seeds.** A broader claim, but too slow a loop when anything needs fixing.
  Rejected for levels 2, 3, and 5 on 20 seeds, with the scope stated wherever the numbers appear (D2).
- **The development seeds** (0 to 299). More familiar cases, but they shaped the rules being measured.
  Rejected for fresh seeds fixed before any benchmark ran.

## Consequences

- Every published number is recomputable from `benchmarks/results/chaos-results.json`, whose digests
  are checked whenever it is read, and CI proves on every change that the committed numbers still come
  from the code.
- A change that alters an outcome on the smoke cells needs `make bench` run again before CI passes.
- The product gains no mode for the benchmark: `PlaywrightSession.pin_handle` is the one adapter method
  added for harnesses, beside `pinned_handle`.

### Limitations of the measurement

- **The chaos portal is ours.** It is a synthetic site this project wrote, and its changes are the
  mutations this project chose; the grid says how Mendwork does on those, not on every release of every
  site.
- **Scale.** Two workflows of 9 and 6 steps, three levels, 20 seeds each. A per-level figure rests on 40
  runs per system.
- **The scripts are verified.** They check the workflow's own checkpoints, so they stop after a caught
  wrong action; an unasserted script would make more. Their wrong actions are a lower bound.
- **The role + name script is one author's choice.** It uses a label for fields without a role, as
  Playwright's documentation advises; another author might use a placeholder or a test id.
- **The ground-truth chooser is not a model.** Its column is an upper bound on what Rung 3's rules allow.
- **The model column** is one small local model on one 8 GB machine, at temperature 0. Its latency
  depends on memory pressure, and no hosted model's cost was measured (there is no key).
- **Latency is not comparable across kinds of system.** Mendwork's includes settling, identity checks,
  screenshots, and heal evidence; a script's does not. The ground-truth reads around every action add a
  few milliseconds to every system.
- **"Visible" is a box and a style.** An unnecessary abstention needs the real control attached, with a
  non-empty box and computed `visibility: visible`; that is not Playwright's full actionability
  (enabled, stable, receiving events). A decision stop whose control is not visible counts as a
  failure, not an unnecessary abstention.
- **Page-recorded wrong actions** are counted around each action. One recorded between actions, by a
  timer, would be missed; the portal records only on activation.
- **A cell that cannot explain itself is refused.** Every run that did not succeed records the step it
  stopped at, its stop kind and its reason, including a navigate, which acts on no control and so has
  no outcome class; the results document will not validate without it, and a reason nobody supplied is
  published as `unrecorded` rather than omitted. The reason is deliberately left out of the outcomes
  digest, because it can carry text that differs between runs of the same cell, such as the port a
  local server happened to bind.
- **The smoke gate covers 20 cells.** A change can alter other cells without failing CI; `make bench`
  gates every cell when it runs.
- **Determinism** is shown by two full runs on one machine and by the 20 smoke cells on every CI run,
  not on every platform.
- **One cell in 748 differed between two runs, once.** An earlier pair of runs disagreed on a single
  cell: the `role_name` script at level 5, seed 1000+14, `download_report`. One run completed
  `fill_email` and abstained at `fill_password`; the other reached nothing, its whole run lasting
  226 ms, because the first navigate failed. Re-running that cell alone reproduced the completing
  behaviour **5 times out of 5**, so it was a transient local navigation failure, not a difference in
  what the systems decided. Neither run produced a wrong action, and the `single_mutations` section's
  outcomes digest was identical across both; only the grid's differed, by that one cell. Diagnosing it
  took those five re-runs because a navigate step acts on no control, so nothing recorded why the run
  ended — which is why every cell now carries a `run_failure` and no cell may report a failure it
  cannot explain.
- **Sleep corrupts a run, and the clock says so.** A later run finished the gate clean but took
  3,720 s of wall time for 477 s of CPU. Its log held nine stalls over 20 s, totalling 2,921 s, the
  longest 994 s: with `displaysleep 5` and `sleep 1`, once the display slept `powerd` released its
  "prevent sleep while display is on" assertion and the machine slept underneath the run. That is not
  only a timing problem — a step straddling a stall reaches its 10 s timeout for no reason, and that
  run reached two fewer `role_name` steps and one fewer completed `ladder_free` changed step than a
  clean run of the same code. It was discarded, not published. The published runs hold sleep off for
  their whole duration (`caffeinate -dimsu`), and a run whose wall time far exceeds its CPU time is
  treated as corrupt rather than slow.
- **One real-application pair, four runs.** The Gitea pair is one workflow on one upgrade of one
  application: eight labelled steps per system, not a sample anything can be inferred from. It is
  published for the failure mode it exposed, not for its rates.
- **The pair stopped at step 4**, so its later steps — including the only irreversible step in the
  benchmark — are unmeasured on a real application, and its per-system rates rest on three or four
  reached steps each.
- **A person's label can decline to answer.** On a real application an action whose label resolves to no
  element, or to several, is recorded as `ground_truth_unknown` rather than as wrong. No action in the
  published Gitea run was unknown, so no published number rests on that rule; it exists so that a future
  pair with a flakier label cannot manufacture a wrong-action count.
- **The recorded CSS selector baseline is limited by our recorder,** which emits a CSS selector only for
  elements with an id. Where a page's controls have none, that baseline cannot express the step at all,
  and the scorecard says so rather than counting it as a failure to find the element.
- **Not measured:** patching, approval flows on a real application (the example workflows have no
  irreversible step, and the pair never reached its own), and sites with frames or shadow DOM.
