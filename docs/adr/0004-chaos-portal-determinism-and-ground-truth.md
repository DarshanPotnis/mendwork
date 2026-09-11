# 4. Chaos portal determinism and ground truth

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

The benchmark's headline metric is the wrong-action rate, and it must be exactly zero. That
number only means something if three things hold:

1. **The page is reproducible.** A result can be rerun and gets the same page, on any
   machine and on any date.
2. **The benchmark knows the right answer for every step.**
3. **The answer is invisible to the thing being measured.**

A mutation engine that used `Math.random`, rendered a date, or tagged its targets with
`data-chaos-*` attributes would quietly break one of these. It would also look like it
worked.

## Decision

- **Seeded randomness, split by page and purpose.**
  - Every random choice comes from mulberry32 streams seeded by FNV-1a hashes of
    `(seed, pageId, purpose)`.
  - Selection and each mutation have their own stream, so changing one mutation's draws
    does not shift another's.
  - A page's mutations are identical no matter which page the visitor came from.
- **One abstain page per seed, from the seed alone.** At levels 4 and 5, the page that
  receives the abstain_expected mutation comes from a stream seeded by the seed with no
  page id. Every page computes the same answer without coordination, and a workflow run
  meets exactly one abstain case.
- **Aspect claims prevent conflicts.**
  - Mutations claim the aspects of a target they change: element, attributes, label,
    ancestry, position.
  - Overlapping claims are refused, and abstain mutations claim everything.
  - Application order is fixed, independent of selection order.
- **No time, locale, or unseeded randomness.**
  - Data, default ranges, and formatting are constants and hand-written functions.
  - A static guard bans `Date`, `Math.random`, `performance.now`, `Intl`, `toLocale*`,
    and `crypto` as code tokens in portal JavaScript; only the Chaos button may use
    `crypto`.
  - Browser tests compare DOM hashes across timezones, locales, and a clock set five
    years ahead.
- **Ground truth through JavaScript only.**
  - `window.__chaos` exposes the seed, level, abstain page, applied mutations, wrong
    actions, and `locate(targetKey)`.
  - Behaviour is bound through a `WeakMap`, so decoys and dangerous controls look like
    ordinary controls.
  - A test fails if any attribute reveals a target key, a mutation id, or a chaos marker.
  - Mendwork's recorder and healer must never read `window.__chaos`.
- **Wrong actions are harmless and recorded.**
  - Decoys and dangerous controls change nothing. They append to `wrongActions` and show
    a visible notice.
  - `duplicate_plausible` produces two copies that differ only in fresh ids, neither of
    which performs the action.
  - On an abstain_expected step, any activation counts as wrong, whether or not it
    reached `wrongActions`.

## Alternatives considered

- **One global random stream per page load.** Simpler, but a page's mutations would then
  depend on navigation history, and adding a draw to one mutation would reshuffle every
  mutation after it. Rejected: fixtures would churn with every change.
- **Ground truth in DOM attributes** (`data-chaos-target`). Easy to read from tests, but a
  healer could read it too, and so could any scoring feature that looks at attributes.
  Rejected: it would make healing trivial and the benchmark invalid.
- **Choosing the abstain page per page with a probability.** Pages would disagree: some
  seeds would have no abstain case and some would have several. Rejected in favour of a
  seed-only choice every page agrees on.
- **Using `Intl` and `Date` with a fixed timezone and locale.** Fixing them in tests does not
  fix them for a benchmark run on another machine or a developer's browser. Rejected: the
  portal simply never asks.
- **A comment-aware static guard.** It would need a JavaScript tokenizer to handle strings,
  regexes, and templates correctly. It would also open a loophole in exchange for
  convenience. Rejected: comments count, and can say "the clock" instead.

## Consequences

- A benchmark result is reproducible from its URL, and every step has an unambiguous
  expected outcome.
- The portal cannot show real dates or locale-formatted numbers. Formatting is hand-written
  and the dataset is fixed.
- Adding a mutation means declaring its aspects and eligibility. The exact-count test fails
  if any page can no longer meet its level.
- Phase 5 must enforce that nothing in `mendwork` reads `window.__chaos`.
- **Every pair has a seed.** `benchmarks/chaos/heal_pairs.json` records a seed for every
  (heal mutation, eligible target) pair, derived in the browser from the portal's own
  declarations and selection code.
  - A freshness test fails, naming the pairs, when the portal and the table drift apart.
  - A sweep proves every pair keeps its target visible and working.
