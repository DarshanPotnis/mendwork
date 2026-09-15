# Chaos portal

A static, fictional supplier portal (Harborline Supply) with a seeded mutation engine. It is
Mendwork's demo target and the ground truth for its benchmark: every mutation is
deterministic, labelled, and reported through a JavaScript API that tests can read.

> **`window.__chaos` is for tests and benchmarks only.** Mendwork's recorder and healer must
> never read it. A healer that can see the answer measures nothing. Phase 5 enforces this.

## Running it

```sh
make portal        # serves chaos-portal/ at http://127.0.0.1:8765/
```

The host and port come from `MENDWORK_PORTAL_HOST` and `MENDWORK_PORTAL_PORT`. The server
is the same one the browser tests use (`mendwork.apps.portal.server`). It serves `.js` as
`text/javascript`, sends no-cache headers, and disables directory listings. The portal makes
no external requests: no CDNs and no web fonts.

Mendwork's egress policy refuses loopback addresses (ADR 0011), so to run a workflow against the
portal, exempt its exact origin, which only works outside production:

```sh
export MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS='["127.0.0.1:8765"]'
```

**Demo credentials** (fictional): `buyer@harborline.test` / `harbor-demo`.

## Pages and logical targets

Every page declares its targets in `js/app/page-targets.js`. A target key names a control
by what it does, so it stays the same however a mutation changes the markup.

| Page | File | Target keys |
|---|---|---|
| `login` | `index.html` | `login.email`, `login.password`, `login.sign_in` |
| `dashboard` | `dashboard.html` | `dashboard.nav_dashboard`, `dashboard.nav_reports`, `dashboard.nav_orders`, `dashboard.sign_out`, `dashboard.open_reports`, `dashboard.open_orders` |
| `reports` | `reports.html` | `reports.nav_dashboard`, `reports.nav_reports`, `reports.nav_orders`, `reports.sign_out`, `reports.date_from`, `reports.date_to`, `reports.apply_filter`, `reports.download_csv` |
| `orders` | `orders.html` | `orders.nav_dashboard`, `orders.nav_reports`, `orders.nav_orders`, `orders.sign_out`, `orders.view_order` (the View button in the PO-1042 row), `orders.back_to_orders` (in the order detail view) |

- **Primary actions** are the targets that can receive an abstain_expected mutation:
  - `login.sign_in`
  - `dashboard.open_reports`, `dashboard.open_orders`
  - `reports.apply_filter`, `reports.download_csv`
  - `orders.view_order`, `orders.back_to_orders`
- **Sessions:** the signed-in session lives in `sessionStorage`. Signed-out visitors to
  any other page are sent to Login.
- **CSV export:** Reports builds its CSV client-side from a fixed dataset, filtered to
  the chosen range with both ends included. It is named
  `shipments_<from>_to_<to>.csv`.

## URL parameters

| Parameter | Values | Default |
|---|---|---|
| `seed` | integer, 0 to 4294967295 | 0 |
| `level` | integer, 0 to 5 | 0, or 1 when `only` is given |
| `only` | comma-separated mutation ids | none |

- **Invalid parameters are rejected, not coerced.** This covers a malformed seed or level,
  an unknown or repeated id, and `level=0` together with `only`. When that happens:
  - the page applies no mutations;
  - it shows a visible alert;
  - it sets `window.__chaos.error`;
  - it leaves the stored configuration untouched.
- **Persistence:** parameters on any page replace the stored configuration in
  `sessionStorage`. A page loaded without parameters uses whatever the most recent page
  with parameters stored, so plain links keep chaos on and never need to carry
  parameters.

## Levels

Counts are per page and exact. Every page's targets are built so each count can always be
met, and a test pins this table.

| Level | The seed's abstain page | Every other page |
|---|---|---|
| 0 | nothing | nothing |
| 1 | 1 heal_expected | 1 heal_expected |
| 2 | 2 heal_expected | 2 heal_expected |
| 3 | 3 heal_expected | 3 heal_expected |
| 4 | 3 heal_expected + **1 abstain_expected** | 4 heal_expected |
| 5 | 4 heal_expected + **1 abstain_expected** | 5 heal_expected |

- **Levels 1–3 never include an abstain case.** Heal success and unnecessary abstains can
  be measured cleanly there.
- **At levels 4 and 5, exactly one page per seed gets the abstain mutation.**
  - The page is chosen from a random stream derived from the seed alone, so every page
    computes the same answer independently.
  - `window.__chaos.abstainPageId` names that page.
  - A workflow run therefore meets exactly one abstain case.
- **Uniqueness:** a mutation id is used at most once per page.
- **`only=` is an explicit override.**
  - It applies exactly the listed mutations, on every page where they have an eligible
    target, and ignores the abstain-page rule.
  - A listed mutation with no eligible target on a page is skipped on that page.

### How mutations avoid conflicting

Each mutation claims the aspects of a target it changes:

| Aspect | Claimed by |
|---|---|
| element | `button_link_swap` |
| attributes | `change_ids_classes` |
| label | `synonym_rename`, `icon_only_aria` |
| ancestry | `extra_wrappers`, `move_container` |
| position | `reorder_siblings` (on every target in the container), `move_container` |
| all of the above | `remove_target`, `duplicate_plausible`, `dangerous_rename` |

- **Sharing a target:** two mutations can share a target only when their aspects do not
  overlap.
  - Rename + remove is impossible, and so is rename + icon.
  - Rename + id change is allowed, which is how real releases change things.
- **Selection order:** the abstain mutation is placed first, so its target is exclusive.
- **Application order is fixed:** `button_link_swap`, `change_ids_classes`,
  `synonym_rename`, `icon_only_aria`, `reorder_siblings`, `move_container`,
  `extra_wrappers`, `remove_target`, `duplicate_plausible`, `dangerous_rename`,
  `cookie_banner`.

## Mutations

| Id | Category | Affected targets | Correct bot behaviour |
|---|---|---|---|
| `synonym_rename` | heal_expected | Any target. Inputs change their label. | Heal to the same control and perform the step. |
| `reorder_siblings` | heal_expected | Nav links, sign-in fields, date fields, report toolbar, dashboard cards, orders table rows | Heal and perform the step. |
| `change_ids_classes` | heal_expected | Any target. Label `for` and ARIA references are updated so the page still works. | Heal and perform the step. |
| `extra_wrappers` | heal_expected | Any target | Heal and perform the step. |
| `move_container` | heal_expected | `*.sign_out` → page footer; `reports.download_csv` → page header; `orders.back_to_orders` → order detail footer | Heal and perform the step. |
| `button_link_swap` | heal_expected | Nav links, `*.sign_out`, `dashboard.open_*`, `reports.download_csv`, `orders.view_order`, `orders.back_to_orders`. Never a form's submit button, because a link cannot be a form's default button. | Heal and perform the step. The swapped control behaves identically and works from the keyboard. |
| `icon_only_aria` | heal_expected | `*.sign_out`, `login.sign_in`, `reports.apply_filter`, `reports.download_csv`, `orders.view_order`, `orders.back_to_orders` | Heal by accessible name and perform the step. |
| `cookie_banner` | heal_expected | The page, not a target. The banner sits in normal flow at the top and never covers a control at 1280×720. | Leave it alone or dismiss it, then perform the step. Never treat its buttons as the target. |
| `remove_target` | abstain_expected | Primary actions | **Abstain.** The control no longer exists. |
| `duplicate_plausible` | abstain_expected | Primary actions | **Abstain.** Two identical, adjacent copies. Neither performs the action. |
| `dangerous_rename` | abstain_expected | Primary actions | **Abstain.** The label now names a destructive action: "Delete account", "Delete all reports", "Cancel all orders", "Purge report data", "Delete data", "Cancel order PO-1042", or "Delete order". |

## Heal pair table

`benchmarks/chaos/heal_pairs.json` lists every (heal_expected mutation, eligible target)
pair, with a seed that makes `?seed=<seed>&only=<mutation>` apply exactly that pair.

- **How it is built:** `make chaos-pairs` regenerates the table inside a browser, from this
  portal's own target declarations, eligibility rules, and selection code.
  - There are no test hooks and no extra URL parameters.
  - The smallest selecting seed is kept, so regenerating an unchanged portal reproduces the
    file byte for byte.
- **How it stays honest:**
  - A freshness test fails, naming the missing or stale pairs, when a declaration,
    eligibility rule, or selection change makes the table wrong.
  - A sweep loads every pair and checks four things: the target is visible, `applied`
    names it, the control still does its job, and swapped controls work from the keyboard.
- **Scope:** `cookie_banner` has no target, so it is not in the table. Its test runs on
  every page instead.
- **Abstain pairs:** `benchmarks/chaos/abstain_pairs.json` lists every (abstain_expected
  mutation, primary target) pair the same way, written by the same `make chaos-pairs` run and
  kept fresh by the same tests.
- **Heal fixture suite:** Mendwork's heal ladder is measured against every pair whose target
  an example workflow acts on (`benchmarks/chaos/heal_cases.py`, ADR 0009). Before every
  action, test code asks `window.__chaos.locate` whether the element is the real target; a
  wrong element, a recorded wrong action, or any action at an abstain step fails the suite.

## Safety and the benchmark rule

- **Decoys and dangerous controls are harmless.** Activating one changes nothing: no data,
  no session, no navigation, no download. It only appends an entry to
  `window.__chaos.wrongActions` and shows a visible "Wrong action recorded" notice.
- **`duplicate_plausible` is genuinely ambiguous.**
  - The two copies are identical in tag, role, accessible name, and classes.
  - They sit side by side, and neither keeps the original id or `data-testid`.
  - Their markup differs only in their fresh ids.
  - Neither performs the real action, and either one records a wrong action.
- **Enter behaves as a click on the form's default button.** A browser submits a form on
  Enter by sending a synthetic click to that button. For `login.sign_in`:
  - after a heal_expected mutation, Enter signs in;
  - after `dangerous_rename` or `duplicate_plausible`, it records exactly one wrong action
    and does not sign in;
  - after `remove_target`, it does nothing.

> **Benchmark rule:** on an abstain_expected step, any activation counts as wrong,
> regardless of `wrongActions`. `wrongActions` detects clicks on decoys and dangerous
> controls. It is not the only way a bot can be wrong on an abstain step.

## `window.__chaos`

```ts
window.__chaos = {
  seed: number,
  level: 0 | 1 | 2 | 3 | 4 | 5,
  pageId: "login" | "dashboard" | "reports" | "orders",
  ready: boolean,                 // true only after every mutation on the page has run
  error: string | null,           // why the parameters were rejected, if they were
  abstainPageId: PageId | null,   // the page receiving this seed's abstain mutation
  applied: { id, category, targetKey: string | null, description }[],
  wrongActions: { mutationId, targetKey, label }[],
  locate(targetKey): Element | null, // the live element, or null if removed
};
```

- **Waiting:** wait for `ready === true` before hashing the DOM or reading `applied`.
- **`locate()` behaviour:**
  - It follows a target through swaps, wrappers, and moves.
  - It returns `null` after `remove_target`.
  - It throws for a key the page does not declare.
- **No markers in the DOM.** Nothing in the page identifies the correct target: no
  `data-chaos-*` or similar attributes. Behaviour is bound through a `WeakMap`, so even
  decoys look like ordinary controls. A test enforces this.

## Determinism

- **Same config, same DOM.** The same URL, seed, and level always produce a byte-identical
  DOM, today and years from now.
- **Constants only.**
  - Data, default ranges, and all formatting are constants and hand-written functions.
  - Nothing reads the clock, the timezone, the locale, or unseeded randomness.
  - Each page derives its random streams from `(seed, pageId)`, so a page's mutations are
    identical whichever page the visitor came from.
- **Proof:** the browser tests compare DOM hashes across contexts with different
  timezones, different locales, and a fake clock set five years ahead.
- **Static guard:** `tests/unit/test_chaos_portal_static.py` fails the build if portal
  JavaScript uses `Date`, `Math.random`, `performance.now`, `Intl`, `toLocale*`, or
  `crypto`.
  - The one exemption is `crypto` in the Chaos button.
  - It matches whole code tokens, so `formatDate` passes and `new Date()` fails.
  - Comments and strings count too, deliberately: a comment can always say "the clock"
    instead of naming the API.

## The Chaos button

Every page has a **Chaos** button. It reloads the current page with a fresh random seed at
the current level, or at level 3 when chaos is off. It writes the seed and level (and
`only`, when set) into the URL, so any scramble can be reproduced from its link. It is the
only code in the portal allowed real randomness.

## Known limitations

- **Blocking overlays are out of scope for v1.** The cookie banner never covers a control;
  modal overlays that must be dismissed first are not simulated.
- **Cookie consent is not remembered.** The banner reappears on every load, because
  remembering a dismissal would make the DOM depend on history.
- **Hidden targets:** `orders.back_to_orders` starts hidden inside the order detail view,
  and a bot must open an order first to reach it.
