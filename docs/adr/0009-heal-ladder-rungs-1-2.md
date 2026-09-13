# 9. Heal ladder Rungs 1 and 2: acceptance, safety overrides, recovery, and one danger vocabulary

- **Status:** Accepted
- **Date:** 2026-09-13

## Context

Phase 3 stops whenever Rung 0 cannot safely proceed: nothing found, several elements found,
or the recorded selectors agree on an element whose identity drifted. Phase 5 turns those
three outcomes into inputs to a free heal ladder: Rung 1 (alternate selectors derived from
the fingerprint) and Rung 2 (similarity scoring over live candidates). Rung 3, a model
choosing among candidates, is Phase 6.

A heal that clicks the wrong control is worse than no heal. The review asked for the failure
modes to be designed out, not tuned out:

- a score threshold chosen by looking at the chaos portal, which would measure nothing;
- two look-alike controls, where any choice is a guess;
- a control renamed to its dangerous opposite ("Export ledger" becoming "Delete ledger") that
  keeps its id and test id, so it still scores well;
- a button that became a link, which may or may not do the same thing;
- a password typed into a visible field;
- a heal that cannot be verified, or that fails verification after it acted;
- a sign-in step retried until the account locks;
- the risk classifier and the healer disagreeing about what is destructive.

## Decision

### When healing runs

- Rung 0 is unchanged. Its `TargetNotFound`, `AmbiguousTarget`, and
  `TargetDrifted(identity_changed)` go to the ladder. `PageNeverStable` does not: nothing on
  a page that keeps changing can be compared safely. Neither does a target that changed or
  detached between verification and action.
- A not-found Rung 0 still waits for the page until the step deadline (ADR 0007). Healing
  then has its own budget, `MENDWORK_HEAL_TIMEOUT_MS` (30 s), capped by the run deadline.
- When the run deadline has already expired, the failure is a run timeout, not a heal.

### Candidates

- **The contract.** `BrowserPort.scan_candidates(CandidateQuery(action, limit))` returns up
  to `limit` visible elements the action could receive, in document order, each pinned with
  its identity (unconfirmed) and facts, plus the total count. `BrowserPort.element_facts`
  moved up from the recording port, because replay now reads facts too.
- **The adapter.** `js/extract_candidates.js` returns only element references and a count:
  a non-empty box and computed `visibility: visible` (Playwright's rule), outside `[inert]`,
  pre-filtered by action. Each element is then read with the existing `element_identity.js`
  and `element_facts.js`, so there is no second role, name, or facts logic, and no field's
  content is read.
- **The engine decides.** `engine/healing/candidates.py` keeps the candidates the action can
  act on (`heal_policy.action_accepts`); the page script's filter only narrows the list.
- **The cap is a capability limit.** A page with more candidates than
  `MENDWORK_HEAL_CANDIDATES_MAX` is never healed: the ladder abstains with
  `candidate_cap_reached`. Scoring part of a page cannot prove the margin, because an element
  left out could be the target or a look-alike. The default, 4,000, was chosen from
  measurements (findings below): it is 2.3 times the heaviest page measured, and a full scan
  at the cap costs about 10.5 s, so two attempts fit in the 30 s heal budget.

### Rung 1

- Alternates come from the fingerprint's own clues: test id, role and full name, role and
  visible text as a substring (when it differs from the name), label (fields), placeholder,
  exact text (not fields), and a stable `#id` or `tag[name]`. Each is also tried inside every
  `within` scope the recorded selectors use. Recorded selectors are left out.
- They are evaluated with Rung 0's consensus (`decide`) inside one consistent reading.
- **Rung 1 accepts only the recorded identity:** the alternates agree on one element, its
  identity matches the fingerprint exactly and is confirmed by Playwright, it reaches the
  accept threshold (so the same name somewhere unrelated is not enough), no safety rule
  refuses it, and it has not failed verification in this step. No margin: uniqueness by
  identity rules out a look-alike.
- An element the alternates agree on whose identity differs, or that scored too low or was
  refused, becomes a Rung 2 candidate.
- **Why Rung 1 exists, although it resolved nothing on the portal** (finding 5): the recorder
  already keeps every selector it can derive that resolved uniquely at record time, so on a
  fresh recording Rung 1 has nothing new to try. It earns its place where the recorded list is
  not the recorder's complete, current list:
  - **hand-written or imported workflows**, which often carry one or two selectors (a css id,
    a test id) while the fingerprint still holds the name, label, or text a working selector
    can be built from;
  - **recordings whose selectors have gone stale**: a selector the recorder dropped because it
    matched several elements (a "View" button repeated in every row) can become unique after a
    release removes the duplicates, and a scoped variant can find a control whose recorded
    scope still exists;
  - **fingerprints recorded before a selector strategy existed**, or edited by hand.
  - It costs a handful of selector evaluations and runs before any page scan, and it accepts
    only the recorded identity itself, so it can never accept something Rung 2 would refuse.

### Rung 2: features and weights

Each feature is a pure function in `engine/healing/features.py`, from 0 to 1. Text is compared
after Rung 0's normalization with rapidfuzz's token-sort ratio, rescaled above a noise floor
(`MENDWORK_HEAL_NAME_SIMILARITY_FLOOR`, 0.5): unrelated labels share letters by chance, and
chance agreement counts as nothing.

| Group | Feature | Weight | Definition | Clue not recorded |
|---|---|---|---|---|
| Wording | name | 0.25 | accessible names (visible text when there is no name) | 0 |
| | label | 0.05 | label texts | the name similarity |
| Identity attributes | attributes | 0.25 | share of recorded `id`, `name`, `data_testid`, `autocomplete`, `href`, `aria_label`, `placeholder` with the same value; identifiers compare exactly | 0 |
| Kind | role | 0.10 | same role 1; within button/link/menuitem 0.5; else 0 | tag and type, as Rung 0 compares role-less fingerprints |
| | tag_type | 0.05 | same tag and effective type 1; same tag 0.5; else 0 | never missing |
| Context | nearby_text | 0.10 | each recorded nearby text's best match, averaged | the structural path similarity |
| | structural_path | 0.10 | Indel similarity over the ancestor levels | never missing |
| Position | position | 0.10 | 1 at the recorded box centre, 0 at `MENDWORK_HEAL_POSITION_SCALE` (0.25) away | 0 |

- The score is the weighted sum, rounded to nine decimals so comparisons never hinge on float
  noise. Ranking is by score, then by each candidate's signature, never by page order.
- A clue the recording did not have scores 0. Groups are never renormalized: a fingerprint
  that recorded less has a lower ceiling rather than inflated scores. Renormalizing was
  rejected because, with no attributes recorded, context alone would reach 0.625.

### The acceptance rule and where it comes from

The groups are what a release changes independently: wording, identity attributes, element
kind, ancestry, position. The weights, threshold, and margin follow from three invariants,
written before any portal score was seen:

- **A. One lost clue still heals.** A candidate identical except for one whole group scores
  1 minus that group's weight; the largest group is wording (0.30), so the floor is **0.70**.
- **B. Context never establishes identity.** With no wording and no identity attributes in
  common, the best possible score is kind + context + position = **0.45**.
- **C. Weak clues never separate look-alikes.** The largest position or context weight is
  **0.10**.

Hence:

- **`MENDWORK_HEAL_ACCEPT_THRESHOLD` = 0.60:** above B's ceiling, at or below A's floor, and
  leaning towards abstaining (the midpoint would be 0.575). A candidate that lost its wording
  may also have moved anywhere on the page, but not also lost its attributes.
- **`MENDWORK_HEAL_ACCEPT_MARGIN` = 0.15:** above every weak clue (0.10), below what an
  identity difference opens (kind 0.15, attributes 0.25, wording 0.30).
- **Settings enforces B and C** through the engine's `acceptance_problems`, which
  `HealingConfig` also applies: it refuses weights whose role, tag/type, nearby-text, path,
  and position sum reaches the threshold, a margin not larger than every nearby-text, path,
  and position weight, and weights that do not sum to 1.
- `tests/unit/healing/test_calibration.py` computes where synthetic archetypes land, and
  property tests prove B and C for every configuration Settings accepts.

**The accept rule.** Ranked best first:

1. the top candidate must itself pass every safety rule, or the ladder abstains
   (`top_rejected`): when the element most like the recorded one is refused, a person should
   look;
2. its score must reach the threshold;
3. it must lead the best other candidate that no safety rule refused by at least the margin
   (a refused candidate is proven not to be the target, so it cannot narrow the margin);
4. its identity must be confirmed by Playwright (only the winner is confirmed, because it
   costs a locator query).

### Safety rules that override score

Pure functions in `engine/safety/heal_policy.py`, applied per candidate by
`engine/healing/checks.py` and reported as a `SafetyRejection`:

1. **Danger words.** Refused when the candidate's name, visible text, or label adds a danger
   word none of the recorded texts had, read through `risk.danger_words_in` with the soft-verb
   rule.
2. **Identifiers.** Refused when the recorded texts and the candidate's each have a number the
   other lacks: "Open invoice INV-7780" is another row's control, not "INV-2231".
3. **Kind.** Controls have an interaction class: submit, activate, toggle, text entry,
   date-like, choice, other. A change of class is refused: a link cannot send a form, a button
   cannot flip a checkbox. Dates keep their exact input type; toggles and other controls their
   exact tag, role, and type. A fingerprint recorded without a role is compared by tag and
   type, as Rung 0 compares it.
4. **Button ↔ link.** Within submit or activate, a change of element (a button that became a
   link, or the reverse) is allowed only when the step has an effect checkpoint:
   `url_matches`, `element_visible`, `text_present`, `download_completed`, or
   `response_received`. The class rule rules out behaviour a click cannot reproduce; the effect
   checkpoint proves the activation was equivalent, because the heal counts only once it passes.
   The change is reported (`kind_change: button → link`).
5. **Credentials.** A fill that types a credential (a secret value, or a field
   `detect_secret_field` flags) may heal only to a masked field, and only one a selector can
   mask in screenshots; a plain value never goes into a masked field.
6. **Unconfirmed identity.** A winner Playwright cannot confirm is refused.

### Gates and limits after acceptance

In order, before anything acts:

- **Verification must be possible.** A heal needs a checkpoint that can prove it: an effect
  checkpoint, or `field_has_value` for a fill. `no_error_banner` alone proves only that nothing
  visibly went wrong. Otherwise the step abstains (`unverifiable`).
- **Irreversible steps never act on a heal.** The step stops as `awaiting_approval` with a
  `HealProposal` (candidate, features, score, margin) in the run record; the run stops as
  `AWAITING_APPROVAL` and exits 4. The approval flow is Phase 7.
- **Attempt limits.** `MENDWORK_HEAL_MAX_ATTEMPTS` (2) healed targets per step, counting those
  that fail verification. An authentication step gets `MENDWORK_HEAL_AUTHENTICATION_MAX_ATTEMPTS`
  (1, or 0 to never heal one): a fill of a credential, or a click or key the classifier reads
  as changing the session (`risk.authentication_reason`, including a live element that submits
  a form holding a password). Repeated attempts can lock the account (ADR 0006).

### Verification and recovery

- A healed target goes through the same pre-action checks, action, settle, and checkpoints as
  one Rung 0 verified. `heal_verified` is emitted once the checkpoints decide.
- A healed target that fails its pre-action checks was never acted on: it is excluded, and the
  ladder runs again.
- A heal that fails its checkpoints is excluded by signature (what identifies it on a reloaded
  page), and:
  - **IRREVERSIBLE:** the run ends `NEEDS_REVIEW` and exits 4; it is never retried. The risk
    gate means this cannot normally happen; the policy is still applied to any healed action
    that ran, and a test injects a gate that lets one through to prove it.
  - **SAFE and CAUTION**, within the attempt limit, the last known-good state is restored:
    1. every step records the document and URL it began on; the steps that began on the failed
       step's document are its segment;
    2. a CAUTION step clears the field its failed fill typed into, if the field is still
       attached and editable, so a wrong value neither stays on screen nor gets submitted;
    3. the segment's first URL is re-opened with `navigate_with_retry`, which discards unsaved
       state, and the page must land on exactly that URL;
    4. the segment's earlier steps are replayed quietly (no events, no new results, downloads
       discarded), each resolved by Rung 0 or by its own verified heal, with every checkpoint
       passing again;
    5. Rung 0 runs again on the restored page; if it now succeeds the step proceeds without a
       heal, otherwise the ladder runs with the failed candidate excluded.
  - **Restore is refused** when the segment holds an irreversible step (replaying would repeat
    it), and **fails** when the page lands elsewhere, a replayed step cannot resolve, or a
    checkpoint fails. The step then abstains (`restore_failed`); nothing is retried blindly.

### Reuse within a run

- No reuse across steps: every step heals on the evidence of the page it acts on, and a heal
  costs a fraction of a second.
- A step's verified heal is remembered (its signature) only for replays of that step inside a
  restore: there, a Rung 2 winner with the same signature is reused without counting as a new
  heal attempt, and still passes the threshold, margin, safety rules, and checkpoints. Without
  this, restoring past a healed sign-in would exceed the authentication limit. If the page
  differs, the remembered element is not found, the replay fails, and so does the restore.

### One danger vocabulary

- `risk.py`'s private helpers became `danger_words_in`, `session_phrases_in`, and
  `authentication_reason`; `classify_risk` calls them, and so does `heal_policy`.
- The healer receives the same `RiskVocabulary` built from `MENDWORK_RISK_*`.
- `tests/unit/safety/test_danger_vocabulary.py` proves it: no collection literal in the healer
  holds two or more vocabulary words; `heal_policy` binds the classifier's own functions; for
  every generated name the classifier's IRREVERSIBLE-by-danger and the healer's introduced
  danger agree; and one Settings change moves both.

### Evidence and output

- **Domain** (`engine/domain/heals.py`): `HealAttemptReport` per rung (outcome, the top
  `MENDWORK_HEAL_REPORT_CANDIDATES` with per-feature scores and any rejection, considered and
  on-page counts, chosen, runner-up, score, margin, threshold, required margin, kind change,
  verification), `RecoveryReport`, `HealProposal`, and `HealReport` on `StepResult`. Evidence
  models moved to `engine/domain/targets.py` so run and heal records can share them.
- **Statuses and errors:** `RunStatus` and `StepStatus` gain `AWAITING_APPROVAL` and
  `NEEDS_REVIEW`; errors gain `HealAbstained` (with a `reason`), `ApprovalRequired`, and
  `NeedsReview`. Exit code 4 means the run stopped for a person.
- **Events:** `heal_attempted` per rung, emitted when the rung decides; `heal_verified` after
  the checkpoints; `state_restored` after a restore. Decision and verification are separate
  events because the action happens between them, and an event must not report an outcome
  that has not happened. The run record merges them.
- **Human output:** every rung's line, the winner's score, margin, features, and kind change,
  and `HEALED at rung N` once verified. An abstention lists what was compared and ends with a
  next step chosen by its reason (re-record, add a checkpoint, raise the candidate cap or heal
  timeout, sign in by hand, or approve the proposal once approvals arrive in Phase 7); a test
  pins the line for every reason. No guidance suggests lowering the threshold: it applies to
  every heal.

### The heal fixture suite

- **Cases** (`benchmarks/chaos/heal_cases.py`): every heal_expected pair in
  `heal_pairs.json` on a target download_report or view_order_detail acts on (48), cookie_banner
  on each page they act on (4), and every abstain_expected pair on those targets (15).
  `make chaos-pairs` now also writes `abstain_pairs.json` with the portal's own selection.
- **Segments.** Each case replays the committed steps on the mutated page, up to its step. The
  sign-in page is opened by the workflow's own first step; other pages by one inserted navigate
  carrying `?seed=&only=`, with the portal session seeded by the harness. Complete workflows
  measured about 1.4 s each, which would have put `make check-all` near 200 s; end-to-end
  fidelity stays with the level-0 runs and seed 0 at level 3, run whole.
- **Ground truth at the moment of action.** A BrowserPort wrapper asks
  `window.__chaos.locate` whether each pinned element is the running step's target before
  every click, fill, select, or press, and reads `wrongActions` before the context closes. Any
  miss, any recorded wrong action, and any action at an abstain step is a wrong action and
  fails the case.
- **Concurrency.** Cases run in separate browser contexts, four at a time, once per session;
  each test reads its own case's outcome.
- **Result (2026-09-13):** 52 of 52 heal_expected cases resolved (Rung 0: 38, Rung 2: 14),
  15 of 15 abstain_expected cases abstained, 0 wrong actions. Rung 1 resolved none (finding 5).
- **Combinations, with the same ground truth** (`python -m benchmarks.chaos.rung0_seeds`, which
  now checks every action and reports wrong actions and false successes): seeds 0–19 at level
  3 checked 154 actions and at level 5 checked 98, with 0 wrong actions and 0 false successes (a
  step that passed its checkpoints while acting on the wrong element).

## Findings worth keeping

1. **Candidates on real pages** (click scan, 1280×720, measured 2026-09-13): chaos portal
   pages 4–18 at level 0 and 6–21 at level 5; Hacker News 230; a GitHub repository page 284;
   MDN's HTML elements reference with its sidebar 599; Wikipedia's population table 1,769.
2. **Scan cost.** About 2.1–2.7 ms per candidate on macOS (identity and facts are two page
   evaluations each); scoring 1,769 candidates took 0.03 s. The scan, not the scoring, is the
   cost.
3. **Seed 0 at level 3** stopped in Phase 3 because `button_link_swap` made Download CSV a
   link: test id, text, and css still hit, but the role drifted. It now heals at Rung 2
   (score 0.85, margin 0.63) and the download checkpoint proves it.
4. **Role-less fingerprints.** The first suite run refused two date-field renames: the kind
   rule compared the fingerprint's missing role with the page's `textbox`. Rung 0 compares
   such fingerprints by tag and type, and the kind rule now does too.
5. **Rung 1 resolved 0 of 52 heal cases** (measured 2026-09-13). 38 resolved at Rung 0 and never
   reached the ladder. Of the 14 that did (`button_link_swap` ×4, `synonym_rename` ×10), Rung 1
   had no alternates at all in 10, and reported `not_found`. In the other 4, the two
   view_order_detail steps, it had four or five alternates, but they are built from the same
   name, text, id, and scope the mutation changed, so they agreed on the same drifted element
   and handed it to Rung 2, which healed it. The golden recording of download_report
   (`tests/fixtures/recordings/download_report.yaml`) has no derivable alternates on any step:
   the recorder already keeps every selector that resolved uniquely when it recorded. The
   hand-written examples match the recorder's selector list except for the three role-less
   fields, where they omit the `role_name` selector the recorder adds; since those fingerprints
   record no role, Rung 1 derives nothing there either.

## Alternatives considered

- **Tuning weights and thresholds on the portal.** Quick to make green, but it measures the
  portal, not healing. Rejected for invariants, enforced by Settings.
- **Renormalizing weights over the clues a fingerprint recorded.** Fairer to sparse
  fingerprints, but context alone could then reach 0.625. Rejected.
- **Scoring the first N candidates of a large page.** Heals more pages, but cannot prove the
  margin against what it did not see. Rejected for abstaining at the cap.
- **Margin over every candidate, refused ones included.** Stricter, but a refused look-alike
  proven not to be the target would block a clear winner, while a refused top candidate
  already stops the ladder. Rejected.
- **Allowing any button ↔ link change.** Heals more, but a link cannot submit a form. Rejected
  for the class rule plus an effect checkpoint.
- **A single `heal_attempted` event after verification.** One event per rung, but live output
  would show an action before the decision behind it. Rejected.
- **Saved DOM snapshots for the fixture suite** (the original plan). Fast, but ground truth
  needs `window.__chaos.locate`, and verification needs the portal's real behaviour (downloads,
  navigations, form handlers). Rejected for live cases.
- **Complete workflows per case.** Most faithful, about 90 s for 67 cases. Rejected for
  segments, keeping complete runs where they matter.
- **Reusing a heal for every later step with the same fingerprint.** Cheaper, but a later page
  earns acceptance on its own evidence. Rejected; reuse is limited to replays inside a restore.
- **Dropping Rung 1**, since it resolved nothing on the portal. Rejected: that result reflects
  recorder-complete selector lists on a portal whose releases do not un-duplicate controls, not
  workflows written by hand, imported, or recorded against an older release.

## Consequences

- A heal is always a proposal until verified, and every decision is recomputable from the
  evidence recorded.
- **Known limitations:**
  - Danger detection is a vocabulary. A control that keeps its test id and is renamed to a
    destructive action outside the vocabulary scores like a harmless rename; the IRREVERSIBLE
    gate, the checkpoint requirement, and Rung 3 are the remaining defences.
  - A fill's `field_has_value` proves the value landed, not that the field was the right one.
    Fills rely more on scoring, the credential rule, and later checkpoints.
  - The identifier rule refuses counters that change ("Refresh (3)" to "Refresh (5)"); such
    steps abstain.
  - Restoring re-opens a URL. Single-page apps whose state is not in the URL, and pages that
    redirect a signed-in visitor, fail to restore, and the step abstains.
  - Pages above the candidate cap are never healed; the cap and the heal timeout must be raised
    together on slow machines.
  - Shadow DOM and frames are outside the scan, as they are outside Rung 0.
  - Rung 2 resolves every single mutation on the portal; combinations that change both wording
    and identity attributes of one target abstain by design (invariant B) and are Rung 3's input.
- **Tests and budgets.** The fixture suite is `slow` and runs concurrently. The chaos portal's
  determinism module moved to `slow` to keep `make check` within a minute (D1 of the Phase 5
  plan); it still runs in `make check-all` and CI. The coverage ratchet covers `engine/healing`.
