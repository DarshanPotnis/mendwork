# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-09-10

## Context

`ARCHITECTURE.md` describes what Mendwork is and how it is built, but it describes the
design as it stands now. It does not say which alternatives were weighed, or why one was
chosen. That reasoning is the part that decays first: months later the decision looks
arbitrary, someone reverses it without knowing what it was protecting against, and the
original problem comes back.

This project is also built one phase at a time, across separate sessions, by a mix of
people and AI assistants. Nobody involved can be assumed to remember the argument behind
a choice, so the argument has to live in the repository rather than in anyone's head.

## Decision

We record every architecture decision not already covered by `ARCHITECTURE.md` as a
numbered Markdown file in `docs/adr/`, named `NNNN-short-title.md`.

Each record has four sections, as specified in `ARCHITECTURE.md` section 18:

- **Context** — what forced a decision
- **Decision** — what we chose
- **Alternatives considered** — with tradeoffs
- **Consequences** — what gets easier or harder

Records are immutable once accepted. A decision that no longer holds is superseded by a
new record that links back to it, rather than edited in place. When a record changes
`ARCHITECTURE.md`, both are updated in the same commit.

## Alternatives considered

- **Decisions in commit messages only.** Free, and already required by our commit
  conventions. But commit messages are ordered by time, not by topic, and answering
  "why is the engine not allowed to import Playwright?" means archaeology through
  `git log`. Rejected as the primary record, kept as a supplement.
- **A single `DECISIONS.md` file.** Simpler to skim while there are few decisions. It
  grows into a file nobody reads, and concurrent edits collide on one file. Rejected.
- **A wiki or an external document.** Easy to write in, but it drifts out of sync with
  the code, is not reviewed alongside a change, and is not available offline or in a
  clone. Rejected.

## Consequences

- Reviewing a design change means reviewing a file in the same pull request as the code,
  so the reasoning is critiqued when it is cheap to change.
- Every non-obvious decision costs a few minutes of writing. This is deliberate: a
  decision that cannot be explained in four short sections probably is not settled.
- New contributors, human or AI, can read `docs/adr/` in order and reconstruct how the
  system reached its current shape.
