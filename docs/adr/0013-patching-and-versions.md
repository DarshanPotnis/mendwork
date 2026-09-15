# 13. Patching and versions

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

Phase 8 turns a verified heal into a new workflow version, so rerunning the same page needs no heal
and no model call (ARCHITECTURE.md §9). Seven questions had to be answered before anything was
built:

- **What becomes a version.** Which heals qualify, what the change record holds, and how an
  irreversible step's heal waits for its approval.
- **Where the new target comes from.** The new version's step needs selectors and a fingerprint for
  the healed element, and there is already one implementation that derives them: the recorder's.
- **When.** Promotion is `immediate` or `after_n_successes`, and a patch waiting for its successes
  must be tried first on later runs without ever acting more loosely than a heal would.
- **File-based runs.** `mendwork run workflow.yaml` runs a file a person may have written or edited
  by hand. Writing heals back into it would overwrite their work.
- **Crash safety.** A process can stop between publishing a version and writing the run's final
  record.
- **History.** People must be able to list versions, compare two, and roll back, including after
  versions were published that they have not seen.
- **Evidence.** A self-contained HTML report shows what each run did, with the healed element
  outlined, and must never carry a secret or load anything.

## Decision

### A verified heal becomes a change record

A heal qualifies when all of these hold (`engine/patching/eligibility.py`):

- **The run succeeded (D2).** Every step after the heal passed too, which is further evidence for
  it, most of all on a weakly verified step. A heal from a run that failed, stopped for a person, or
  was cancelled is reported `run_not_succeeded` and never saved.
- **The run saves heals.** Its workflow source says so (below); otherwise the heal is `not_saved`
  with the reason.
- **Its step succeeded on the healed element,** so the step's checkpoints verified it.
- **The element was fingerprinted** (below); otherwise `not_capturable`, with the problem.
- **An irreversible step's heal had an approval that acted and was verified.** Otherwise
  `not_approved`. The change record carries the approval's proposal id, audit entry, and decision
  time, and `heal_version` refuses an irreversible heal without one.

Every heal that does not qualify is reported in the run's `patches` with its reason, so none
disappears silently.

A `HealChange` records the step id, the rung, the old and new fingerprints, the checkpoint kinds
that verified it and their strength, the score, margin, threshold, and required margin (Rungs 1 and
2) or the model's usage (Rung 3), the approval, the evidence (the run id, `report.html`, and the step
and found screenshots), and the promotion (the policy and every run that verified it). A new target
equal to the old one is not a change and is refused.

`heal_version` (`engine/domain/lineage.py`) derives the child through the same core as
`edit_version` and `roll_back_version`: the next number, the parent untouched, every step id kept in
order. Only the step's target changes. Its intent, risk, value, and checkpoints are kept, because
the heal was verified against them, and the retargeted step is validated again as a whole. The
parent's target for the step must be the change's old target.

**Weakly verified heals (D3)** are saved, and marked weak in the run summary, the report, `history`,
and `diff`: a `url_matches` or `field_has_value` check proves where the page went or what a field
holds, not which element was used (ADR 0010).

### Selectors are re-derived by the recorder

Just before a healed target is acted on, while the element is still pinned and the page is the one
the heal was accepted on, `HealCapture` calls the recorder's own `TargetRecorder.record`
(`engine/patching/capture.py`). The recorder derives the element's selectors exactly as recording
does, keeps only those that resolve in one consistent reading to exactly this element (ancestor
scoping included), and proves the fingerprint resolves back to it through Rung 0. There is no second
implementation to drift from the first.

- `TargetRecorder` now takes a `TargetCaptureContext` (browser, timer, scrubber, and timeouts), which
  both the recording context and the step runner can build. `scope_ancestors` and a new
  `element_view` moved up to `BrowserPort`.
- Capture reads the page and never acts on it. It runs after the heal's gates and before the action,
  and a problem never fails the step: `no_selector`, `identity_unconfirmed`, `unrecordable_target`,
  `element_gone`, `page_never_stable`, or `secret_in_target` only mean the heal cannot become a
  version.
- A found element whose text holds a value the run resolved from a secret is never stored.
- Capture also takes the found screenshot the report outlines the element on (below).

### Promotion

`MENDWORK_PATCH_PROMOTION` chooses the policy (D8: default `immediate`).

**`immediate`.** When the run finishes, each qualifying heal becomes a child of the latest version,
one version per heal, in step order.

**`after_n_successes`.** A qualifying heal becomes a pending patch, and becomes a version once
`MENDWORK_PATCH_PROMOTION_SUCCESSES` succeeded runs verified it (D8: default 3, at least 2; 1 would be
`immediate`).

- **Storage.** `<store>/.pending/<workflow_id>.json`, replaced atomically under an exclusive `flock`
  on `<workflow_id>.lock`, both created 0600 (`adapters/storage_fs/pending_patches.py`). The port is
  `PendingPatches`, with a hold whose one `replace` stores everything the hold decided, so two runs
  finishing at once cannot both count one success or both publish one patch.
- **Identity.** A patch is keyed by its step id, the SHA-256 of the whole step as it was when the heal
  was verified, and the digest of the found target. A patch whose step changed in any way no longer
  matches it.
- **First tries (D7).** A later run of the same step tries pending targets before the ladder, most
  successes first, then the oldest, but only after the recorded target's Rung 0 verdict is not found
  or drifted, never after an ambiguous verdict. A pending target is resolved by Rung 0 in its
  non-patient mode, which decides on one consistent reading, and must pass the step's checkpoints.
  - A pre-action failure (not found, not actionable) falls back to the heal ladder.
  - A failed checkpoint on a SAFE or CAUTION step is recovered like a failed heal.
  - A failed checkpoint on an IRREVERSIBLE step stops the run `needs_review` with reason
    `pending_patch_unverified`, and it is never retried.
- **Counting.** Each succeeded run that verified a patch, through a first try or by healing to the
  same element, counts one success for it. A patch is removed when its step changed, when the latest
  version already targets its element, or when a succeeded run found the recorded element again.
- **Only saving runs try.** A run that saves no heals (`--exact`, a file that differs) tries no
  pending patch.

**Placing a patch on the latest version** (`engine/patching/rebase.py`):

| Latest version's step | Placement | Result |
|---|---|---|
| Exactly the step the heal was verified against | `publish` | A child of the latest |
| Already targets the healed element | `already_applied` | Nothing published |
| A rollback restored it after undoing exactly this target (D4) | `previously_rolled_back` | Nothing published |
| Anything else | `stale` | Nothing published |

A heal is never rebased onto a step that changed: it was verified against the old content. When
another process publishes first (`VersionConflict`), placement runs again on the new latest version,
at most `MENDWORK_PATCH_PUBLISH_ATTEMPTS` times (D8: default 5), then the result is `conflict`.

**A heal a rollback undid is never saved again automatically (D4).** Its placement is
`previously_rolled_back`, and the run says how to restore the version that had it if it was right
after all.

A workflow store or pending-patch file that cannot be read or written never changes a run's outcome
or exit code: its heals are reported `store_unavailable`.

### The ordering guarantee

**A version is published before anything that says it was.** Promotion runs when the run has
finished and before its final record is written:

1. The version is published atomically and without overwriting: written to a temporary file in its
   workflow's directory, synced, and given its final name with `link(2)`, which fails if the name
   exists (ADR 0006).
2. Then the pending patches are replaced: a temporary file, synced, renamed over the document, and
   the directory synced.
3. Then the run's final record is written, carrying every patch outcome.

A process that stops between any two steps leaves a valid version and records that do not mention
it. The record is still `running`, and `mendwork show` completes it from its journal as ADR 0011
describes. The next promotion reconciles without duplicating or losing the patch:

- **`immediate`:** placement finds the latest step already carrying the healed target and reports
  `already_applied`.
- **`after_n_successes`:** the pending patch left behind no longer matches the latest step, whose
  target is already the healed one, so it is removed ("v2 already targets this element").

Promotion runs in a shielded task: a first Ctrl+C during it waits for it to finish, so an interrupt
never lands between steps 1 and 3 by itself.

`tests/unit/patching/test_promotion_crash.py` proves this for both policies. A child process runs a
real replay with the file store and ends itself with `os._exit(137)` immediately after the publish
returns. The test then asserts:

- the store holds v1 and v2, and v2 is valid;
- `run.json` is still `running`, with no patches;
- the next run reports `already_applied`, or removes the stale pending patch;
- the store still holds exactly v1 and v2.

### File-based runs (D1)

A file given to `mendwork run` is **never written**. Versions live in the workflow store,
`MENDWORK_WORKFLOW_STORE_DIR` (D8: default `workflow-store`) or `--store-dir`:
`<store>/<workflow_id>/v0001.yaml`, `v0002.yaml`, and so on. The file is matched to its workflow's
stored versions **by content**, the decoded declarations and steps. Comments and formatting never
count as an edit, and a file edited by hand while it still says `version: 1` is never taken for
version 1.

| The store holds | What runs | Heals saved |
|---|---|---|
| Nothing for the workflow, and the file is a first version | The file, published first as v1 | Yes |
| Nothing, and the file is a later version | The file as written | No: `mendwork import` stores it |
| A version with the file's content, and every later version came from a heal or a rollback | The latest version | Yes |
| A version with the file's content, but a later version was imported | The file as written | No: the person pointed at an older file on purpose |
| No version with the file's content | The file as written | No: `mendwork import` makes it the latest |
| Anything, with `--exact` | The file as written | No |
| A store that cannot be read or written | The file as written | No |

- **The run says which applied.** Its record carries a `WorkflowSource` (the path, the matched
  version, whether a stored version ran, whether heals are saved, and why not). `mendwork run` prints
  a notice when a stored version runs or heals are not saved (to stderr with `--output json`), and
  the report shows the source.
- **A first version is published before the run starts.** Two processes publishing the same first
  file write identical content, which is a no-op; a conflicting first version sends the choice back
  to the store. Publishing happens before input and secret preflight, so a first run that is then
  refused for a missing input has still stored the file as v1. That is harmless, because v1 is
  exactly the file.

**`mendwork import <file>`** makes a file its workflow's latest version, as a hand edit
(`ManualEdit`, summary "Imported from <file name>" or `--summary`). It is the one command that can
cut a file loose from the versions heals made, so first it prints:

- the version it creates;
- each step that differs from the latest version, in the diff's words;
- the pending patches on those steps, which will stop matching and never become versions;
- and, when the file has an earlier version's content, that a rollback records the same thing while
  keeping the heals it undoes from being saved again.

Then it asks `Import? [y/N]`. Anything but `y` or `yes`, including the end of input, imports nothing
and exits 1; `--yes` answers for scripts. The file's step ids must be the latest version's, as for
every version (ADR 0006). An import planned against v3 fails with `VersionConflict` if another
process publishes v4 first: nothing is imported, and the person runs it again to see the new
difference.

### History, diff, and rollback

These commands exit 0 when done, 1 when a person declined or another process published first, 2 for
an invalid or unknown workflow or version, and 3 when the store cannot be read or written.

- **`mendwork history <workflow>`** lists every version, newest first, with why it exists and the run
  behind each heal. For the latest version it lists each step's checks with their strength (strong,
  weak, or not verified), warns when any step is weakly verified, and lists the pending patches that
  still match, with the runs that verified them.
- **`mendwork diff <workflow> <from> [<to>]`** compares steps by id, in plain words. For an element it
  shows kind, name, text, label, identifiers, type, nearby text, and place in the page on each side,
  marks what changed, and pairs each selector with the other side's selector of the same strategy.
  Every heal published between the two versions says why: the rung, the check that confirmed it and
  its strength, the numbers, the model's usage, and the approval. Versions in between are listed.
  Nothing a person typed into a field is ever quoted: a literal value is described, not shown.
- **`mendwork rollback <workflow> --to <version> [--reason]`** publishes a new version with the
  restored version's content. Nothing is deleted, and a rollback is undone by rolling back again.
  - **It is safe when the current version is newer than the run being examined.** The new version
    follows the latest version, and the command first lists every version it undoes, including any
    published after the run a person looked at.
  - **It never retries on a conflict.** If another process publishes first, nothing is rolled back and
    the command exits 1, because retrying would undo a version the person was never shown.
  - Its reason is kept in the version (D6: rollbacks are not written to the audit log until Phase 10
    adds per-tenant users).

The words are shared (`engine/patching/words.py`), so the CLI, the report, and the history say the
same thing the same way.

### The run report

`report.html` is written beside `run.json` when a run ends, when an approval resumes it, and when a
rejection ends it. It is rebuilt from the record and `workflow.json` by a pure view model
(`engine/reporting/view.py`) and rendered by `adapters/report_html`. It shows:

- a step timeline with each step's duration, result, and checkpoint strength;
- each step's checks and result, how its target was found, and its error and evidence;
- for a heal, the found element outlined on its screenshot, the recorded and found fingerprints side
  by side with what changed, the ladder rung by rung, and what a model was shown, answered, and cost;
- approval decisions and what came of them;
- the patch outcomes, the workflow source, and the run's model cost. A call whose provider reported
  no token counts reads "token counts not reported by this provider", never 0 tokens, and a call
  with no configured price reads "cost unknown (no price in MENDWORK_MODEL_PRICES)", never a dollar
  figure. When only some calls have counts or a price, the words give what is known and for how
  many calls, and name the rest. The diff and the CLI's usage line use the same distinctions.

**It loads nothing.**

- The stylesheet is inline, screenshots are PNG `data:` URIs, there is no script, and fonts are the
  reader's own.
- Artifact paths are shown as text, never as links.
- A Content-Security-Policy meta element refuses every fetch but `data:` images:
  `default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`.
  A mistake in the markup still could not reach the network.
- `tests/integration/test_report_browser.py` and the guarantee test load reports in Chromium, with
  the policy and with it removed, and record no request.

**Secrets never appear.**

- Every text is escaped when it is rendered, and the whole document is scrubbed with the process's
  scrubber, which holds every secret the command resolved.
- Screenshots are substituted in afterwards, through placeholders, so scrubbing can neither corrupt
  base64 nor be skipped for it.
- Screenshots were masked when they were taken: password fields always, plus every selector the step
  masks.
- A step's value is described by where it came from, never shown.
- The approval leak test searches the reports and the workflow store too.

**The found screenshot** is a full-page capture clipped to a viewport-sized window centred on the
element and kept inside the document, so the element is in the picture wherever it sits, and the
page is never scrolled to take it. The outline is the element's visible part, as fractions of the
window.

**Budget (D5).** Screenshots are embedded most telling first (found elements, the stopping step,
healed steps, then the rest) until `MENDWORK_REPORT_SCREENSHOTS_MAX_BYTES` is used (default 8 MiB;
measured portal screenshots are 47–67 KB, so at least 125 fit). Any other is named, with why it is
not shown.

### The guarantee, proven

`tests/integration/test_patching_guarantee.py` runs the committed example against the chaos portal
in-process, through the same source resolution and Patcher as `mendwork run`, with every action
checked against ground truth.

- **Level 3 seed 3.** Rung 3 heals `open_reports` with one call to the ground-truth model, and v2 is
  published. The rerun on the same seed runs v2 and counts zero in every place a heal or a model
  call could appear:
  - heal reports;
  - candidate scans;
  - first tries;
  - the run's model usage;
  - calls to the model;
  - new entries in the model-call ledger.
- **Level 0 seed 0, and the rule seed.** The rule seed is the smallest level-3 seed other than 3
  whose chaos mutates the target, by ground truth; it is seed 2. In both, v2's target no longer
  matches: the run heals again at Rung 2, publishes v3, and acts on no wrong element.
- **`after_n_successes` with N = 2, on level 3 seed 0.**
  - First run: heals, and leaves the patch pending at 1 of 2.
  - Second run: tries the patch first, needs no heal, and publishes v2.
  - Third run: runs v2 with no heal and no first try.

`tests/integration/test_cli_patching_browser.py` does the same through the CLI and a scripted
OpenAI-compatible server on loopback. The server receives one request on the first run and none on
the rerun. A run after `rollback --to 1` heals again and reports `previously_rolled_back`.

## Alternatives considered

- **Write heals back into the YAML file.** It would overwrite comments, formatting, and edits made
  while a run was in progress, and a file under version control would change under its owner.
  Rejected (D1).
- **Match a file to versions by its `version:` number.** A file edited by hand keeps its number, so an
  edited step would be silently replaced by a stored one. Rejected: content decides.
- **Always run the latest stored version, whatever the file says.** An edited file would never run.
  Rejected.
- **Derive the new target from the candidate scan's description.** A second selector implementation
  that could drift from the recorder's and was never proven to resolve uniquely. Rejected in favour
  of the recorder's own derivation.
- **Count heals from failed runs toward promotion.** A later step's failure may mean the heal chose
  the wrong element. Rejected (D2).
- **Rebase a heal onto a step changed since it was verified.** The heal was verified against other
  content. Rejected: such a patch is `stale`.
- **Re-promote a heal a rollback undid when a run verifies it again.** A person deliberately undid it.
  Rejected (D4).
- **Retry a rollback on a conflict.** It would undo a version the person was never shown. Rejected.
- **A report with external assets or script.** It would load from the network and could leak.
  Rejected: inline CSS, `data:` images, no script, a refusing policy.

## Consequences

- A promoted heal costs nothing on its page again: the rerun resolves every step at Rung 0.
- The workflow store is state worth backing up: deleting it loses the history and the heals, though
  every file still runs as written. Phase 10 moves versions and pending patches to Postgres, behind
  the same ports.
- A file edited by hand saves no heals until it is imported; `mendwork run` says so on every run.
- Every run writes an HTML report; its screenshots use disk only up to the budget.
- `mendwork approve` takes `--store-dir` like `run`, and must be given the store the run used; with a
  different store the heal is reported `stale` rather than saved, never saved to the wrong lineage.

### Limitations

- Pending patches and their locks are files, so promotion under `after_n_successes` is coordinated
  only between processes that share the directory, and `flock` needs a POSIX system.
- A heal whose element cannot be fingerprinted is never saved; a later run heals it again.
- Rollbacks are recorded in the version history only, not in the audit log, until Phase 10 (D6).
- A first run publishes its file as v1 before preflight, so a run refused for a missing input has
  still stored it.
- A report embeds screenshots only up to its budget, and a report is rewritten, not versioned, when
  an approval or rejection changes its run.

## Verification

On the 8 GB M2 used for ADR 0012, in the foreground with four workers:

| Target | Wall | Tests | Engine | Engine domain | Overall |
|---|---|---|---|---|---|
| `make check` | 45.7 s | 2120 | 97.99 | 98.60 | 93.61 |
| `make check-all` | 123.6 s | 2389 | 98.13 | 98.60 | 95.53 |

- **The heal fixture suite.** 0 wrong actions. Every heal case resolved, and each of its 14 heals
  fingerprinted the element it acted on.
- **The coverage ratchet.** It holds for every file in `engine/patching` and `engine/reporting`,
  from unit tests alone.

**Finding: pages without a doctype.** The element-view browser test failed at first: its window
came out as tall as the whole page. `set_content` pages have no doctype, so they render in quirks
mode, where the root element's client size is the document's. `element_view.js` now reads the
viewport from the body in quirks mode, as CSSOM View specifies. The portal's pages have a doctype,
so no portal test could have shown this; many real pages have none.
