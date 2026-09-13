# 8. Recording semantics: capture, verification at record time, secrets, and verify-by-replay

- **Status:** Accepted
- **Date:** 2026-09-12

## Context

Phase 4 adds `mendwork record`: a person does a task once in a headed browser and gets a
workflow that Phase 3's replayer runs without hand-editing. A recorder that merely writes
down events produces files that look right and fail later. Three failure modes shaped this
decision:

- **Races.** A click on a link starts a navigation before any selector can be checked
  against the page it was made on.
- **Unverified guesses.** A selector that matched once, a checkpoint nobody ran, or a role
  Playwright computes differently, all fail at replay time, far from their cause.
- **Leaks.** A recorder that reads what is typed and then filters it has already received the
  password.

### Findings worth keeping

Measured with Playwright 1.62.0 and Chromium 151 before and while building this phase.

1. **`expose_binding(..., handle=True)` no longer exists.** A binding carries JSON only, so
   the page cannot hand Python an element; Python fetches it through Mendwork's namespace.
2. **Chromium runs without a back-forward cache under Playwright.** Playwright launches it
   with `--disable-back-forward-cache`. Four back and forward traversals all reported
   `pageshow.persisted === false`, and the init script ran in every restored document.
3. **The binding's global is configurable.** `window.<binding>` can be captured by the init
   script and deleted before the page runs; the captured function still delivers. Playwright
   itself adds `__playwright__binding__` and `__playwright__binding__controller__`.
4. **Holding a gesture back and performing it from Python works.**
   - A trusted `pointerdown`, `pointerup`, and `click` with `preventDefault()` and
     `stopImmediatePropagation()` at window capture never reach the page; Chromium also
     suppresses the compatibility `mousedown` and `mouseup`.
   - Python then clicked the same element through Playwright, and the page received exactly
     that click and navigated. The same held for a raw `mouse.click` and for Enter in a field.
   - Playwright's hit-target check on the scripted person's own `page.click` did not hang.
   - A binding call left pending while its document navigated away raised nothing.
5. **Chromium says who started a navigation.** `Page.frameRequestedNavigation` fires for a
   link click, `location.assign`, and a meta refresh; it does not fire for `goto`, back, or
   reload.
6. **Password and date inputs have the role textbox.** The Phase 3 identity script computes
   `role: textbox` for both, and Playwright's role locator confirms it. The hand-written
   examples omit the role for them.
7. **Element boxes depend on the platform.** The portal uses the `system-ui` font stack, so
   `bbox` values differ between macOS and Linux.
8. **Found while building:** a heading repeated by the step (two identical "Results")
   produced two identical proposals that each waited the full checkpoint timeout, and the
   start step's intent embedded the literal start URL, port and all, even though the URL
   becomes a run input.

## Decision

### Capture: hold back, verify, perform

- **One page script, installed everywhere.** The recorder registers `expose_binding` and
  `add_init_script(recorder.js)` on the context before its page exists, so every document of
  every frame has the recorder before its own scripts run.
- **Clicks and keys are held back and performed by the recorder.** A plain click, or Enter,
  Escape, or Space (Space only on a control that is not a text field), is stopped at window
  capture and reported. The page then treats the gesture as busy. The recorder verifies the
  target, **arms** the page (its own action may reach that element), performs the action with
  the replayer's primitives, **disarms**, settles, and records the step. Finishing the step
  answers the binding call, and the page is idle again.
- **Fields are not intercepted.** Typing stays native. An `input` event marks a field
  edited; its `change` event (commit), or any held-back gesture, reports it once. Keystrokes
  therefore merge into one FILL by construction. Consecutive fills of the same element in
  the same document also merge (the later value wins, keeping the earlier id).
- **Interactions that are not steps** are decided in the page from markup alone. No role or
  accessible-name logic is duplicated: roles and names come from `element_identity.js`.

  | Interaction | Result |
  |---|---|
  | Click with no clickable ancestor (background) | passes through, nothing reported |
  | Click on a text field, select, or native picker (date, time, color, range) | passes through; its value arrives as a FILL or SELECT |
  | Click on a child of a control | the closest `a[href]`, `button`, `input`, `select`, `textarea`, `summary`, role-bearing, tabindex, or contenteditable ancestor |
  | Click on a label | its labelled control, under that control's rules |
  | Click with a modifier or another button, double-click, file input, anything in a frame, anything started while a step is being recorded | held back, **ignored** with a notice, recording continues |
  | Enter in a textarea or contenteditable, Tab, editing keys | pass through, not steps |
- **Two kinds of problem.**
  - **Ignorable:** the interaction had no effect because it was held back, so the person can
    repeat it. The terminal says how, and the summary counts them. A click on a hidden,
    disabled, or covered control is ignored the same way.
  - **Fatal:** recording ends and nothing is written. The causes are:
    - no selector survives;
    - Playwright does not confirm the identity;
    - the page never stops changing;
    - a new tab or window opens;
    - the browser navigates in the middle of a step;
    - a page is restored from the back-forward cache;
    - an element vanishes before its step is recorded, so its effect happened unrecorded;
    - a page message the recorder does not understand.
- **No double recording.** Messages carry a per-document token and a gap-free sequence
  number, and the adapter releases them in order and drops repeats. Events that reach the
  armed element are the recorder's own and are never reported. A click is reported only when
  its own `pointerdown` was held back. A second `init` in one document is a no-op, because
  the recorder's namespace slot is non-configurable.

### One page global

`window.__mendwork` becomes a namespace with one non-configurable slot per script:

- `pageState`, from Phase 3's `page_state.js`;
- `recorder`, from `recorder.js`.

Page scripts cannot import each other, so the few lines that create the namespace are
repeated in both scripts between `mendwork-namespace` markers, and a test keeps the copies
byte-identical. The binding global is captured and deleted at install time. A browser test
asserts that the only globals a recording page sees are `__mendwork` and Playwright's own two.

### Navigations

- The adapter logs every main-frame document commit (`Page.frameNavigated` without a parent)
  and marks it as started by the page or by the browser (finding 5).
- A recorded click or key is a **window**: from its "before" observation to the observation
  after the page settles.
  - A commit inside the window belongs to the step, so a navigating click is one step with a
    `url_matches` proposal.
  - A browser-started commit inside a click or key window is fatal.
- Outside any window:
  - a browser-started commit (address bar, back, forward, reload) is a NAVIGATE step to its
    literal URL, since replay has no back action;
  - a page-started commit is not a step. It is a notice, because replaying the step before it
    makes the page navigate again.
- The start URL is step 1, opened with the replayer's `navigate_with_retry`. An unreachable
  start URL is unusable.

### Selectors: verified, scoped, proven

- **Candidates, ranked:**
  1. test_id;
  2. role_name with the full accessible name (confirmed identities only);
  3. role_name with the element's **own text** (its direct text nodes) as a substring, when
     that differs from the name;
  4. label (form controls);
  5. placeholder;
  6. text (not for fields, at most 128 characters);
  7. css: `#id` for a stable-looking id (no `:`, five-digit runs, or eight-character hex
     runs), else `tag[name="…"]` for a named control.

  The own-text candidate exists for repeated controls whose names differ only by text for
  screen readers ("View" plus a hidden " order PO-1042").
- **Verification.** Each candidate is resolved with `resolve_unique` inside a settle / read /
  re-read bracket; a snapshot the DOM changed under is read again until the step deadline,
  then `page_never_stable`. A candidate is kept only when it finds exactly the recorded
  element (checked with `group_identical`). One that finds nothing or a different element is
  dropped with its level counts.
- **Scoping.** A candidate that matches several elements is tried inside the target's
  ancestors (up to `MENDWORK_RECORD_SCOPE_ANCESTORS_MAX`), in this order:
  1. rows by row-header text as a substring;
  2. named containers (region, form, navigation, dialog, listitem, article, table, …);
  3. test ids;
  4. stable ids.

  Nearest comes first within each kind, and cells are never scopes. If a scope is itself
  ambiguous, it is scoped again, at most two levels deep. On the portal, "View" matches 12
  buttons and becomes `role_name button 'View' within row 'PO-1042'`.
- **Proof.** The fingerprint built from the survivors must resolve through Phase 3's
  `resolve_target` to the same element with a confirmed identity. A step is known to replay
  at the moment it is recorded.

### Checkpoints: proposed from observations, verified at record time

- **Proposals**, from observations before and after the step:

  | Signal | Proposal |
  |---|---|
  | The path changed | `url_matches`, `https?://[^?#]+<escaped path>(?:[?#].*)?`; a query-only change proposes nothing |
  | A heading or named landmark became visible | `element_visible`, headings first, deduplicated, up to three alternatives |
  | A live region's text changed without a navigation | `text_present` |
  | A download completed (awaited within the step timeout) | `download_completed`, the escaped file name |
  | The action submits a form | `no_error_banner`, checked last |
  | A fill | `field_has_value`, compared inside the page; non-emptiness only for a secret |

- **Verification.** Every proposal is checked with the replayer's `evaluate_checkpoint`
  (the download against the download already collected), within
  `MENDWORK_RECORD_CHECKPOINT_TIMEOUT_MS` (1 s). The page has already settled and been
  observed, so a failing proposal rarely passes by waiting.
- **Failures are dropped, never written.** A failing proposal is dropped with its reason; a
  step with no checkpoint is allowed and warned about.
- **No interference.** Verification only reads the page, and while it runs the page holds
  back the person's next gesture (dropped as busy) rather than letting it race the checks.

### Secrets never reach Python

1. **No message has a value field.** Page messages are pydantic models with
   `extra="forbid"`, so a message that carried a value is rejected and ends the recording.
2. **The recorder cannot read field content.** `recorder.js` never reads `.value`,
   `selectedOptions`, `innerText`, `textContent`, or `FormData`; a static guard enforces
   this.
3. **Credential fields are never read.** The engine decides before reading:
   `detect_secret_field(fingerprint)`, or the page's value-free "masked" fact (a password
   type, a credential autocomplete, or `-webkit-text-security` other than `none`, which is
   inherited, so a masked ancestor counts), makes the fill a secret reference.
4. **One gated read.** Only other fields are read, through `field_text.js`, which repeats the
   mask check in the same synchronous call and reports `masked` instead of the content.
   The check is byte-identical to `element_facts.js`'s, under a test.

**Proof at the boundary.** Every payload the page sends or a recording script returns
passes through an `InboundObserver` before parsing; production logs its source and size. A
browser test types a distinctive secret into three fields:

- a password field;
- a CSS-masked field with a neutral label;
- a field inside a masked container.

It then searches the whole transcript in raw, JSON, HTML, percent-encoded, UTF-16, and
base64 forms. The plain email must appear in the transcript, which proves that value reads
are observed.

A slow test repeats the search across the written file, the summary, stdout, stderr, the
notices, the transcript, the verification run's events, and every artifact.

### Naming, writing, and the verify-by-replay guarantee

- **Secrets.** Each secret field is named after recording stops. The default comes from the
  field's name attribute, id, or label.
- **Inputs.** The start URL and values that look like an email or a username are proposed as
  inputs, each with a name and a description. The person accepts both, types `name` or
  `name: description`, or keeps the literal (`-`).
- **Default descriptions** come from what was recorded, never from a host or port:
  - the start URL: "URL of the page the workflow starts on (recorded at /index.html)";
  - a typed value: "Email address typed into the 'Email address' field", with "Username",
    "Date", or "Value" as fits.
- **`--input NAME=VALUE`.** It decides in advance for every recorded literal equal to VALUE,
  with the default description.
- **Prompts.** A plain line per question on stdin; at the end of input the defaults are
  accepted.
- **Validation.** The rules for names are the engine's `NamingSession`.
- **Writing.** The workflow is assembled through the strict document parser, encoded
  canonically, decoded again, and written with the file store's no-overwrite sequence
  (temporary file, fsync, `link`, unlink, fsync directory). `--out` must not exist.
- **Verify by replay.** The recording is reported as successful only after a replay passes.
  That replay uses Phase 3's replayer in a freshly launched browser, with the recorded input
  values and secrets from `MENDWORK_SECRET_*`.
- **Exit codes:**
  - 0: verified;
  - 1: the replay failed at a step (the file is kept, marked NOT VERIFIED);
  - 2: usage, an unusable recording (nothing written), or a missing secret;
  - 3: infrastructure.
- **`--no-verify`** skips the replay with a warning, for steps whose real side effects must
  not repeat.
- **Stopping.** Ctrl+C stops after the step in progress, and a second Ctrl+C aborts. Closing
  the browser window also stops. An in-page stop control was rejected: it would add an
  overlay that changes layout and needs excluding from capture.

### Risk: unknown is CAUTION

Risk stays classified by consequence (ARCHITECTURE §8), as a pure function over signals and
a vocabulary from Settings, in this order:

1. navigate is SAFE; fill and select are CAUTION;
2. a danger word is IRREVERSIBLE on any element, unless every danger word is a soft verb
   ("remove", "reset") with a view-state noun ("Remove filter");
3. signing in or out, or submitting a form that holds a password, is CAUTION;
4. read words, a download, a plain link, or a tab are SAFE;
5. a form control is CAUTION;
6. anything else is CAUTION.

"Continue", "Next", and "OK" are everywhere, and marking them irreversible would make
Phase 5 demand approval so often that approvals stop being read.

## Alternatives considered

- **Recording events without holding them back.** Simplest, and it disturbs nothing, but a
  navigating click destroys the page before any selector can be verified against it.
  Rejected (finding 4).
- **Computing selectors and identity synchronously in the page.** No race, but it needs a
  second implementation of role and name logic and a selector engine, and nothing would
  prove Playwright agrees. Rejected.
- **Re-dispatching the held-back event from the page (`element.click()`).** Untrusted
  events differ from what replay produces. Rejected for the replayer's own primitives.
- **Recording every navigation as a step.** A page's own redirect would replay as a second,
  racing navigation. Rejected for initiator-based attribution.
- **A timing window around each click to decide whether it navigated.** Flaky, and a sleep
  in disguise. Rejected for explicit windows bounded by the recorder's own settle.
- **Filtering values after reading them.** The recorder would still receive the password.
  Rejected for decide-before-read and value-free messages.
- **A second page global for the recorder.** Simpler, but it breaks the one-global rule.
  Rejected for the namespace.
- **Unknown actions as IRREVERSIBLE.** Stricter on paper, worse in practice (approval
  fatigue). Rejected.
- **A prompt library.** Nicer editing, but a new dependency for four questions. Rejected.

## Consequences

- **What makes a recording trustworthy.** Each step was performed, its selectors resolved,
  its fingerprint proven through Rung 0, and its checkpoints passed, all while it was being
  recorded. The whole workflow is then replayed before success is reported.
- **The person cannot act while a step is recorded.** The page holds back clicks and keys
  until the recorder answers, typically well under a second; interactions during that time
  are ignored with a notice.
- **Known limitations:**
  - **Double-clicks.** The first click of a double-click is recorded as a click; the second
    is ignored.
  - **Late downloads.** A download that starts only after the page settles is not detected,
    so its step gets no `download_completed` checkpoint.
  - **Navigations from unconsumed events.** A select or field whose change navigates the page
    before it can be recorded ends the recording.
  - **Unsupported interactions.** Frames, shadow DOM, file uploads, drags, and hover-only
    menus are not recorded. A page that defines its own `window.__mendwork` cannot be
    recorded.
  - **Literal URLs.** NAVIGATE steps the browser started keep their literal URL, query
    string included; review them before sharing a workflow.
  - **Selects.** Select steps get no checkpoint (`field_has_value` is for fills only).
  - **Chromium only.** Navigation attribution uses a CDP session.
  - **The armed window.** A person's click on the same control during the few milliseconds
    while the recorder's own action is armed reaches the page.
- **Golden test.** It ignores `created_at` and every `bbox` (finding 7) and is rewritten
  with `make recording-golden`.
- **Test split.** Every recording browser module is `slow`: the interaction, target, and
  boundary modules, the portal recordings with replays, and the full secret scan. They moved
  out of `make check` to leave its time budget for Phase 5. The recorder's logic stays in the
  fast suite through its unit tests, which the ratchet requires to keep `engine/recording` and
  `engine/safety` at 90% or more on their own.
