# Mendwork

[![CI](https://github.com/DarshanPotnis/mendwork/actions/workflows/ci.yml/badge.svg)](https://github.com/DarshanPotnis/mendwork/actions/workflows/ci.yml)

**Self-healing browser automation.** Record a workflow once, replay it deterministically
for free, repair it when the site changes, and verify every repair before trusting it.

## The problem

Repetitive work on websites without APIs is automated in one of two ways today, and
both have a catch:

- **Fixed scripts** are fast and free, and they break the moment a layout changes.
- **AI agents** that reason through every step adapt to change, but they are slow,
  costly, and inconsistent.

## The approach

Mendwork replays a recorded workflow with no AI in the loop. When a step cannot find
its target, it climbs a cheap-first *heal ladder*: recorded selectors, then selectors
derived from the target's fingerprint, then free similarity scoring over the elements
actually on the page, and only then a model — which never writes a selector, it only
picks from a numbered list of real candidates, and may abstain.

A repair is a proposal until its checkpoint passes. Verified repairs are saved as a new
workflow version, so the next run goes straight to the new target with zero model calls.
Irreversible steps — submit, pay, delete, send — never heal without human approval.

Maintenance cost falls over time instead of growing.

## Getting started

Requires [uv](https://docs.astral.sh/uv/), GNU Make, and Node.js 24 (for the TypeScript
checks of plain JavaScript). Python 3.12 is installed by uv from the pinned
`.python-version`; no system or conda Python is used. With nvm, `nvm use` picks up
`.nvmrc`; `npm ci` refuses any other Node major.

```sh
nvm use        # Node.js 24, from .nvmrc
make install   # Python env, npm tooling, Playwright Chromium, pre-commit hooks
make check     # lint, typecheck, import contracts, JS type-check, and tests
make portal    # serve the chaos portal at http://127.0.0.1:8765/
make chaos-pairs  # regenerate the heal pair seed table after changing the portal
```

Configuration is read from the environment with the `MENDWORK_` prefix. Copy
`.env.example` to `.env` to override anything locally; `.env` is never committed.

| Command | What it does |
|---|---|
| `make fmt` | Format and apply safe lint fixes |
| `make lint` | Lint without fixing |
| `make typecheck` | `mypy --strict` |
| `make imports` | Architecture boundary contracts |
| `make jscheck` | TypeScript check of the chaos portal's JavaScript |
| `make test` | Unit and browser tests with coverage gates |
| `make check` | Everything above — must pass before any phase is done |

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the design, and the source of truth for it
- [`BUILD_PLAN.md`](BUILD_PLAN.md) — the phased build plan
- [`docs/adr/`](docs/adr/) — decision records
- [`chaos-portal/README.md`](chaos-portal/README.md) — the demo target, its mutations, and the benchmark rules
- [`CLAUDE.md`](CLAUDE.md) — working rules for this repository

## Status

Phase 1 of 12: the chaos portal, Mendwork's demo target and benchmark ground truth. The
engine itself starts at Phase 2.
