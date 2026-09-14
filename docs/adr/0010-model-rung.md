# 10. Rung 3: the model as a constrained chooser

- **Status:** Accepted
- **Date:** 2026-09-14

## Context

Phase 5's ladder heals every single mutation on the chaos portal with free heuristics, and
abstains, by design, when a release changes both the wording and the identity attributes of one
control (invariant B of ADR 0009). Six such stops were known (level 3 seeds 3 and 15; level 5
seeds 3, 9, 10, and 11). In each, a person looking at the page would see at once that "Email" is
still "Email address" and "Open reports" is still "View reports". A language model can make that
judgement; it can also be confidently wrong, echo page text it was never meant to see, cost money,
hang, or be told what to do by the page it reads.

The review asked for a design in which the model adds judgement and removes no safeguard:

- the model picks from what the ladder already found, never invents a target;
- nothing it says is trusted on its own, its confidence included;
- a secret never reaches it;
- it cannot run up a bill, and a run that needs no heal never calls it;
- its value is measured against ground truth, on cases chosen before it ran.

## Decision

### The model chooses; it never writes a selector

A selector written by a model is a new target nobody verified: it could match an element no rung
scored, one outside the page's candidate scan, or nothing at all, and whether it is safe could only
be judged after acting on it. An index into a list of elements the ladder has already pinned,
described, scored, and checked against the safety rules cannot hallucinate a target: an answer
outside the list is simply no answer. So `ModelPort.choose_candidate(ChoiceRequest) ->
ChoiceResult(choice, confidence, reason, usage)` takes numbered descriptions and returns a number
or null, and the element acted on is always one Mendwork pinned itself.

### The model provides no safety guarantee

The prompt tells the model to answer null when the closest control would do something more
destructive or less reversible than the recorded one. That line is a hint that makes a good model
abstain more often; it is not a safeguard, and nothing depends on the model obeying it. What
prevents a harmful action runs on the model's pick, whatever the model said:

- the **danger vocabulary** (`risk.danger_words_in`, the same function and words as the risk
  classifier) refuses a pick that names a destructive action the recording did not;
- the **identifier rule** refuses a pick naming another order, invoice, or record;
- the **kind rules** refuse a pick that is another kind of control, and a button ↔ link change
  without an effect checkpoint; the **credential rule** refuses a masked/unmasked mismatch;
- the pick is **read again** after the model answers, so a control renamed while the model was
  choosing is judged on what it is now, and must still read exactly as the model was shown it;
- the **risk gate** stops an irreversible step with a proposal for a person, and the **attempt
  limits** (one for a sign-in step) still apply;
- the **rules only a model's pick faces** refuse a pick from another part of the page, and on a step
  whose checkpoints are weak, a pick that keeps no recorded identifier (see the finding below);
- the **step's checkpoints** decide whether the heal worked, and a failed SAFE or CAUTION heal is
  excluded, the page restored, and the ladder run again.

`tests/unit/healing/test_rung3.py` and `test_rung3_properties.py` prove it with models that ignore
the instruction: a pick that gains a danger word before or during the call, for any number the model
answers, is never accepted. The held-out finding below shows why this matters: the model that was
wrong was polite, confident, and gave a plausible reason; only rules and ground truth told it apart.

### When Rung 3 runs, and what the model is shown

- **Only after Rung 2 declines below the threshold or the margin.** Never after it accepts. Never
  when its closest candidate was refused (`top_rejected`): ADR 0009's rule that a person should look
  applies, and it is the dangerous-rename case, where a model picking the runner-up would act on an
  abstain step. Never on a capped or unstable page, or with no candidates.
- **Eligible candidates only.** A candidate a safety rule refused is proven not to be the target,
  and one sharing no wording or identity attributes with the recording could only be chosen on
  context, which ADR 0009's invariant B already forbids. Refused and context-only candidates are
  never shown, so an ignoring model cannot even name them.
- **No look-alikes.** Two eligible candidates with identical descriptions can only be told apart by
  position. When the best eligible candidate has such a twin, no model is asked; a pick with a twin
  anywhere among the eligible candidates, shown or not, is refused.
- **Gates first.** A step with no checkpoint that can prove a heal, or with no heal attempts left,
  abstains before any call.
- **The list.** The best `MENDWORK_MODEL_CANDIDATES_K` eligible candidates, numbered, each as kind,
  name, label, visible text when it says more than the name, up to three nearby texts, and Rung 2's
  score as "similarity". The step is described by its action and intent; its value never is.

### The prompt, and keeping it stable

`engine/healing/prompt.py` renders a system message and a user message as a pure function of the
step, the fingerprint, and the list; `PROMPT_VERSION` (`choose-candidate/1`) is recorded in the
evidence. Every page and workflow text is scrubbed of the run's secrets (text forms and base64
forms), NFKC-normalized, collapsed to one line, cut at 120 characters, and quoted as a JSON string,
so page text can neither leak a secret nor start a line of its own. Three golden files pin the
rendering, and a fingerprint of the template tied to the version fails the build when the template
changes without a new version. The format bounds live beside the template, not in Settings,
because they are part of the version.

### Reading the answer

One JSON object with exactly `choice` (an integer or null), `confidence` (0 to 1), and `reason` (1 to
300 characters), parsed by `engine/healing/choice.py` for every provider: no fence stripping, no
coercion, duplicate keys and NaN refused. Any other shape, a reply cut off at the token limit, or one
the provider withheld gets one repair call (the original request, the unusable reply, and what was
wrong); a second abstains. Null abstains. A number not on the list abstains without a repair.
Providers that support it constrain their output to the same JSON Schema.

### Budgets

- At most `MENDWORK_MODEL_MAX_CALLS_PER_RUN` (4) calls per run and `MENDWORK_MODEL_MAX_CALLS_PER_DAY`
  (200) per workspace per UTC day. These are policy, not capability: a run that needs more model
  choices than that is better re-recorded, and the day's cap bounds a runaway loop or a free tier.
- A call is reserved before it is made, so a crash never under-counts and a failed call counts (a
  provider may bill it). A repair is a call. The run's count is checked first, in memory.
- The day's count lives behind a `UsageLedger` port: files now (one document per UTC day, updated
  under `flock` with an atomic rename), a table from Phase 10. An unreadable ledger allows no call.
- A used-up budget raises `BudgetExceeded`; the step abstains with a Next: line saying which budget,
  and when the daily count starts again.

### Providers

`adapters/models`: a scripted `FakeModel`, Ollama, Gemini, and OpenAI-compatible servers, each a
wire format behind one `HttpChoiceModel` that owns the time limit (all attempts inside the call's
limit, capped by the heal deadline), retries with backoff and jitter on timeouts, dropped
connections, 408, 429, and 5xx, a circuit breaker, a reply size limit, and usage, latency, and cost.
No model is configured by default: sending page descriptions to any model is a choice a person
makes. With a model configured, building the client opens no connection, so a run that needs no
Rung 3 sends nothing.

## Finding: a link to the same page passed `url_matches`

The held-out run with the local model produced one wrong action, in all three rounds, and it was a
false success: the run reported the step verified while the ground truth said it acted on the wrong
element. Per the stop condition, nothing was tuned to make it pass; the prompt is unchanged.

### What the model chose

Level 5 seed 32, step `open_reports`: click the dashboard's "View reports" link (`a`, id
`open-reports`, test id `dashboard-open-reports`, `href="/reports.html"`, recorded next to "Shipment
reports"), verified by one checkpoint, `url_matches` `…/reports.html`. On this seed the link is gone
from the dashboard; the portal's ground truth marks the step as one that must abstain. Rung 2
declined below the threshold. Two lines were eligible and shown: "View orders" and the navigation
bar's "Reports" link. `qwen3:4b-instruct-2507-q4_K_M` answered 2:

> The recorded control has a name 'View reports' and nearby text 'Shipment reports', and the control
> with name 'Reports' and no nearby text is the closest match in intent and structure, as 'Reports'
> is a core part of 'View reports' and the context of reports is preserved.

The adversarial model, which always picks a line that is not the target, found the same weakness.
Across the 16 seeds it made 5 wrong actions, all at `open_reports`. Three failed their checkpoint
and were undone; two passed it (level 3 seed 43 and level 5 seed 32, each on its second attempt,
each on the navigation "Reports" link). In the fixture suite it acted wrongly on
`remove_target-dashboard.open_reports`. (The evaluation's "false success" counts every wrong action
in a step that finally succeeded, so it reports 5; the picks that actually passed verification on a
wrong element are the 2 above, counted from each decision's own verification result.)

### Why every guard passed it

- **Rung 3 ran**, because Rung 2 declined `below_threshold`, not `top_rejected`: no candidate was
  refused, so nothing said a person should look.
- **It was eligible**: "Reports" shares a word with "View reports".
- **No look-alike**: "View orders" and "Reports" read differently.
- **Every safety rule passed**: no danger word, no other identifier, link to link, no credential.
- **The re-read passed**: it still read as shown, Playwright confirmed its role and name, and the
  DOM had not changed.
- **The gates passed**: a SAFE step, a checkpoint that counts as an effect, attempts left.
- **The checkpoint passed**: the navigation link goes to `/reports.html` too, so the URL after the
  click matched.

Each rule did what it was designed to do. None of them asks whether the element is the recorded one
rather than one that does the same thing, and the step's only checkpoint could not tell the
difference.

### `url_matches` is an unreliable proof of identity

`url_matches` proves where the browser ended up, not what was clicked. Any page with two ways to the
same place (a navigation bar and a card, a breadcrumb and a button, a logo and a "Home" link) has
two elements that pass it. `field_has_value` has the same weakness for fills: it proves a value
landed in the field that was filled, not that the field was the recorded one. A step verified only
by these checkpoints accepts any control with the same effect, so a verified heal on it is weaker
evidence than one verified by `element_visible`, `text_present`, `download_completed`, or
`response_received`, which observe something only the right action produces. Rung 2 was protected
by its score and margin: the navigation link scored far below any threshold. A model's pick has no
margin behind it, so it met the weak checkpoint unprotected.

### The response: two refuse-only rules for model picks

Both live in `engine/healing/pick_rules.py`, run after every other safety rule on the pick as it is
now, and apply to Rung 3 only; Rung 2 keeps ADR 0009's rules. Each can turn an accept into an
abstention and never the reverse, which `test_the_pick_rules_only_ever_refuse` checks by running
every generated page and answer with and without them.

1. **Context veto** (`context_lost`). When the recording has nearby text, a pick sharing none of it
   is refused: it is a control from another part of the page. The navigation link had no nearby
   text; the recorded link had "Shipment reports".
2. **Weak-verification bar** (`weak_verification`). On a step with no strong checkpoint (only
   `url_matches`, `field_has_value`, or neither), a pick must keep the recorded `id`, `name`, or test
   id. This addresses the cause rather than the shape: a wrong element that keeps some recorded
   context (a second "Reports" link inside the same card, say) passes the veto, and on a weakly
   verified step nothing after it would catch the mistake.

### Choosing the bar from data

`benchmarks/chaos/rung3_census.py` recorded every line any model was shown across the known and
held-out seeds and every fixture-suite case, under the ground-truth model and the adversarial one,
with the live element behind each line and whether it was the real target. That is 19 distinct
lines: 18 on weakly verified steps (10 real targets, 8 wrong elements) and 1 on a strongly verified
step (a real target). Each candidate bar was scored on the 18 weak lines:

| Bar on a weakly verified step | Real targets refused | Wrong elements admitted, without the veto | Wrong elements admitted, with the veto |
|---|---|---|---|
| no bar | 0 | 8 | 0 |
| nonzero wording similarity | 1 (L5 s10 `fill_email`, "Username or email") | 8 | 0 |
| any surviving identity attribute | 1 (L3 s80 `sign_in`, "Log in") | 0 | 0 |
| **a surviving id, name, or test id** | **1 (L3 s80 `sign_in`, "Log in")** | **0** | **0** |
| wording and any identity attribute | 2 (both of the above) | 0 | 0 |
| wording and an id, name, or test id | 2 (both of the above) | 0 | 0 |

The wrong elements were "View orders" and the navigation "Reports" on every `open_reports` list:
both share wording, neither keeps an identity attribute (`href` is not counted: it says where a link
goes, which is exactly what a same-destination link shares), and neither keeps any recorded nearby
text. The context veto alone refuses all 8 and no weak-step target; it refuses the one strong-step
target (L3 s15 `download_csv`, whose button moved away from "Date range").

- **Wording is rejected.** It stopped none of the 8 wrong elements, because wording is how they
  became eligible in the first place, and it refused a real target.
- **Requiring both is rejected.** It refused twice the targets and stopped nothing more.
- **An identifier over any identity attribute.** The two tie on this data: no target survived only
  through `autocomplete`, `aria-label`, or `placeholder`. The tie is broken by what those attributes
  mean: they describe a purpose or wording that other controls share (`autocomplete="email"` on a
  sign-in and a newsletter field; `aria-label="Reports"` on a navigation link and a card link), the
  same weakness as `url_matches`. `id`, `name`, and test id are given to one control. `name` cannot
  be dropped: seven of the ten weak-step targets kept only `name` and `autocomplete`.
- **Why a second rule when the veto already stops all 8.** Each rule stops all 8 on independent
  evidence (where the element sits; what it is called in the markup), so the measured wrong picks are
  refused twice over, and a wrong element must now defeat both to act on a weakly verified step.

Nineteen lines from one portal are a small sample; the bar is justified by it, not proven general.
The census and its JSON are reproducible with `uv run python -m benchmarks.chaos.rung3_census`.

### For Phases 8 and 9: checkpoint strength is a property of each step

A workflow whose steps all rely on `url_matches` is weaker than its pass rate suggests: every step
can pass while a same-destination control is clicked. `verification_strength` in
`engine/safety/heal_policy.py` classifies a step's checkpoints as strong, weak, or none. The run
report and `mendwork history` (Phase 8) should show it per step, and the benchmark (Phase 9) should
split its metrics by it and count false successes separately, so a heal success rate earned on weak
checkpoints is never mistaken for one earned on strong ones.

## Measurements

### The local model

Apple M2, 8 GB RAM; Ollama 0.33.3 as a Homebrew service; `qwen3:4b-instruct-2507-q4_K_M` (digest
`0edcdef34593`), 2.70 GiB resident, all of it on the GPU. Requests use temperature 0, seed 0, a
4,096-token context, at most 200 output tokens, `keep_alive` 5 minutes, and the reply schema as
Ollama's `format`. Measured with `python -m benchmarks.chaos.model_latency`, the real Rung 3
request through the product's adapter:

| Candidates shown | Prompt tokens | Cold call | Warm p50 | Warm p95 | Output tokens |
|---|---|---|---|---|---|
| 1 | 330 | 22,570 ms | 2,865 ms | 3,108 ms | 68 |
| 3 | 391 | 16,523 ms | 2,917 ms | 3,133 ms | 72 |
| 5 | 458 | 7,452 ms | 3,452 ms | 3,545 ms | 84 |
| 8 | 553 | 7,651 ms | 3,100 ms | 3,163 ms | 75 |

The first cold call includes reading the model from disk; later cold calls reload it from the page
cache. Output length, not list length, sets the warm time.

`qwen3.5:4b`, the vision-capable model the plan also named, could not answer on this machine: its
server took 71 s to start, spent the time warming a vision encoder, and returned HTTP 500 after three
minutes. It is not usable at 8 GB, which is one reason set-of-marks screenshots stay deferred.

### Defaults that follow

- **`MENDWORK_MODEL_TIMEOUT_MS=30000`.** The worst measured cold call was 22.6 s, so a shorter limit
  would abstain on the first Rung 3 call of a day. The step's 30 s heal deadline caps every call
  anyway, so a longer one would buy nothing.
- **`MENDWORK_MODEL_CANDIDATES_K=5`.** No list in the evaluation had more than three eligible lines.
  Five leaves room at about 40 prompt tokens a line, with no measured cost in time.
- **`MENDWORK_MODEL_MAX_CALLS_PER_RUN=4`.** The most any evaluated run needed was 2, a wrong pick
  and the call after it was excluded.
- **`MENDWORK_MODEL_MAX_CALLS_PER_DAY=200`**, a runaway and free-tier bound, not a capacity.

## Evaluation before the new rules

All runs use `download_report` against the chaos portal, with every action checked against the
portal's ground truth, via `python -m benchmarks.chaos.rung3_eval` and `heal_suite --model`.

- **Held-out seeds**, recorded in `benchmarks/chaos/rung3_holdout.json` before any model ran: at each
  of levels 3 and 5, the first five seeds from 20 upward whose Rung 2-only run stops with
  `below_threshold` or `below_margin`, excluding every seed in the pair tables and every seed a test
  uses. Level 3: 43, 80, 126, 140, 144. Level 5: 21, 24, 26, 29, 32.
- **Ground truth model**: answers the target's number only when exactly one listed line reads like
  it, so it measures what the rules allow a perfect chooser to heal.
- **Adversarial model**: always a line that is not the target, with full confidence.

| Run | Actions checked | Wrong actions | Wrong picks that passed verification | Rung 3 calls | Resolved correctly | Not resolved |
|---|---|---|---|---|---|---|
| Ollama, six known stops (×3, identical) | 28 | 0 | 0 | 6 | 4 | 2 model abstained (L3 s15, L5 s11); 1 look-alikes |
| Ollama, ten held-out (×3, identical) | 59 | **1** | **1** (L5 s32) | 6 | 5 | 3 look-alikes, 1 no eligible candidate |
| Ollama, fixture suite (×3, identical) | – | **1 case** (`remove_target-dashboard.open_reports`) | 1 | 1 | – | – |
| Ground truth, all 16 | 89 | 0 | 0 | 12 | 11 | 5 look-alikes, 1 no eligible, 1 abstained |
| Adversarial, all 16 | 97 | **5** | **2** (L3 s43, L5 s32) | 14 | 9 | 5 look-alikes |

The wrong action stopped the phase; the finding above records it.

## Evaluation with both rules

The same runs, same seeds, same model and prompt (`choose-candidate/1`), after the context veto and
the weak-verification bar. Every Ollama run gave identical decisions in all three rounds.

| Run | Actions checked | Wrong actions | Wrong picks that passed verification | Rung 3 calls | Resolved correctly | Refused, by rule | Other |
|---|---|---|---|---|---|---|---|
| Ollama, six known stops (×3) | 28 | 0 | 0 | 6 | 4 | none | 2 model abstained, 1 look-alikes |
| Ollama, ten held-out (×3) | 48 | **0** | **0** | 6 | 4 | L5 s32 `open_reports` (`context_lost`, a wrong pick); L3 s80 `sign_in` (`weak_verification`, the right pick) | 3 look-alikes, 1 no eligible |
| Ollama, fixture suite (×3) | – | **0 cases** | 0 | 1 | – | `remove_target-dashboard.open_reports` | 52 heal cases resolved, 15 abstain cases abstained |
| Ground truth, all 16 | 82 | 0 | 0 | 12 | 9 | L3 s15 `download_csv` (`context_lost`, the right pick); L3 s80 `sign_in` (`weak_verification`, the right pick) | 5 look-alikes, 1 no eligible, 1 abstained |
| Adversarial, all 16 | 72 | **0** | **0** | 12 | 7 | L3 s3, L3 s43, L5 s32 `open_reports` (`context_lost`, wrong picks); L3 s15 `download_csv` (`context_lost`); L3 s80 `sign_in` (`weak_verification`) | 5 look-alikes, 1 no eligible |
| Adversarial, fixture suite | – | **0 cases** | 0 | 1 | – | `remove_target-dashboard.open_reports` | – |

Fewer actions are checked because a refused step ends its run where it used to act.

### What the rules prevented

Every wrong action and every wrong pick that passed verification is gone: the local model's false
success on L5 s32 and in the fixture suite, and the adversarial model's five wrong actions. The
context veto refused all of them; the fixture-suite case's list held only the two lines the census
recorded, both sharing no recorded nearby text. The weak-verification bar refused no wrong pick in
any run, because the veto ran first on every one. The census shows the bar alone would have refused
all eight wrong elements; its value here is the second, independent refusal, not a measured extra
catch.

### What the rules cost

Two resolutions that were correct before are now refused. Nothing else changed: the local model's
other eight correct resolutions (four known, four held-out) and the ground-truth model's other nine
are unchanged.

1. **Level 3 seed 80, `sign_in`** (held-out). The "Sign in" button became "Log in" and kept neither its
   id nor its test id; the step is verified by `url_matches` and `no_error_banner`. The local model chose
   it correctly in all three rounds, as did the ground-truth and adversarial models, and it passed
   verification. It keeps no recorded identifier on a weakly verified step, so the bar refuses it
   (`weak_verification`), and the run stops at sign-in with a Next: line asking for an effect
   checkpoint or a re-recording. This is the cost the census predicted for this bar.
2. **Level 3 seed 15, `download_csv`** (known). The download button moved away from "Date range", the
   text recorded near it; the step's checkpoint (`download_completed`) is strong. The ground-truth
   and adversarial models chose it correctly; the local model had already abstained on it in every
   round. The context veto refuses it (`context_lost`). This is the veto's cost, also predicted by
   the census: the veto applies to every model pick, whatever the checkpoints.

Of the six known Rung 2 stops, the ground-truth model now heals five and the local model four,
unchanged for the local model. Of the ten held-out seeds, the local model heals four, refuses one
wrong pick and one right one, and is not asked on four.

## Alternatives considered

- **The model writes a selector**, or describes the element to look for. More flexible: it could
  find an element the candidate scan missed. Rejected: the target would be one nobody verified, and a
  hallucinated selector becomes an action instead of an out-of-range answer.
- **A confirm-only model**, allowed only to accept or reject Rung 2's best candidate. A narrower blast
  radius, and on this portal it would have behaved the same for every correct resolution (the target
  was line 1 whenever it was listed). Rejected for the approved design, a chooser among the top K,
  whose extra freedom is bounded by the same rules; worth revisiting if a real model shows a position
  bias the rules cannot stop.
- **Editing the prompt after the held-out wrong action.** Rejected by the evaluation's own stop
  condition: a prompt tuned to the seed that exposed a weakness would hide the weakness, and the
  held-out seeds would no longer measure generalization. The prompt is unchanged.
- **Wording similarity as the weak-verification bar.** Rejected on the census: it stopped none of the
  eight wrong elements and refused a real target.
- **Wording and an identifier together.** Rejected on the census: twice the cost, nothing more
  stopped.
- **Any identity attribute instead of an identifier.** Tied on the census; rejected because
  `autocomplete`, `aria-label`, and `placeholder` name a purpose or wording other controls share.
- **Applying the new rules to Rung 2 as well.** Rejected: a Rung 2 winner already clears a threshold
  and a margin over every other candidate, which kept the navigation link far below acceptance; the
  rules would change ADR 0009's measured results for no measured benefit.
- **The context veto on weakly verified steps only.** It would recover L3 s15 `download_csv`, whose
  `download_completed` checkpoint cannot be satisfied by an unrelated button. Rejected for now: the
  veto is a structural statement about the pick (a control from another part of the page is not the
  recorded one), and a strong checkpoint proves the effect, not the element. Reopen with cases where
  a moved control's only proof is a strong checkpoint.
- **A confidence floor**, possibly stricter on weak steps. Would only reduce accepts, but small
  models' confidence is uncalibrated (the wrong pick on L5 s32 came with a fluent reason), so the
  floor would be tuned on the portal. Rejected; confidence is evidence only.
- **Showing refused candidates for context** ("Delete data" exists where "Download CSV" was). Rejected:
  the list is the set of choices, and a refused candidate is proven not to be the target.
- **Set-of-marks screenshots now.** Rejected for this phase (D5): no measured case needs them, the
  vision-capable 4B model could not answer at all on the development machine, and a screenshot shows
  page content the text list never exposes. Reopen with a measured icon-only case and an image-capable
  provider.
- **A daily count in SQLite, or derived from run records.** SQLite adds a dependency and a schema ahead
  of Phase 10; counting run records cannot reserve a call atomically across concurrent runs. Rejected
  for files under `flock` behind a port.
- **Ollama as the default provider.** A literal reading of principle 9. Rejected (D4): sending page
  descriptions to any model is opt-in, and with no server running every abstention would add a failed
  call.
- **Provider SDKs** (openai, google-genai, ollama). Rejected: three dependencies with three retry and
  timeout behaviours, where one HTTP transport gives every provider the same limits.

## Consequences

- Every Rung 3 decision can be recomputed from the record: prompt version, the numbered list, every
  call with tokens, latency, and cost, the choice, the reason, the rule that refused it, and the
  checkpoints that proved or refuted it.
- A run that needs no model makes no call, and a configured model costs nothing until Rung 2 declines.
- A model's pick faces two rules Rung 2's winners do not. On the evaluated seeds they removed every
  wrong action and cost two correct heals, named above.
- **Known limitations:**
  - The model sees text only. A control with no accessible name, no label, and no visible text gives
    it nothing to compare; such a step abstains.
  - A weakly verified step (`url_matches` or `field_has_value` alone) heals at Rung 3 only when the
    control kept its id, name, or test id. A release that renames a sign-in button and replaces both
    its id and test id stops there (L3 s80), and the fix is a stronger checkpoint or a re-recording.
  - A model's pick that moved away from all of its recorded nearby text is refused even when a strong
    checkpoint would have proven it (L3 s15).
  - The weak-verification bar was chosen from 19 lines on one portal. It stopped no wrong pick the
    context veto had not already stopped; it is justified as an independent second refusal, not by an
    extra measured catch.
  - A wrong control that keeps its recorded nearby text and an identifier, on a weakly verified step,
    passes both rules. None was observed.
  - A cold local model can take most of the 30 s heal budget, leaving no time for a repair call after
    an unusable first reply. Under memory pressure (swap nearly full on the 8 GB machine) calls that
    normally take 3 to 4 s took up to 22.5 s in the re-run; decisions were unchanged.
  - A page whose DOM changes while the model is choosing (a ticking clock, a carousel) abstains.
  - The daily count lives in the artifacts directory: deleting it resets the day's count. File locks
    need a POSIX system.
  - Hosted-provider fixtures follow the documented response shapes; only `make live-providers`
    against a real key proves a provider still sends them.
  - A model can still choose a wrong control that no rule refuses and whose checkpoints fail: on a SAFE
    or CAUTION step the action happens, is excluded, and the page is restored. With both rules, the
    adversarial model caused none on the evaluated seeds.
