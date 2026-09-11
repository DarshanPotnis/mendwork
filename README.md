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

Requires [uv](https://docs.astral.sh/uv/) and GNU Make. Python 3.12 is installed by uv
from the pinned `.python-version`; no system or conda Python is used.

```sh
make install   # create the environment, install dependencies and pre-commit hooks
make check     # lint, typecheck, import contracts, and tests
mendwork --version
```

Configuration is read from the environment with the `MENDWORK_` prefix. Copy
`.env.example` to `.env` to override anything locally; `.env` is never committed.

| Command | What it does |
|---|---|
| `make fmt` | Format and apply safe lint fixes |
| `make lint` | Lint without fixing |
| `make typecheck` | `mypy --strict` |
| `make imports` | Architecture boundary contracts |
| `make test` | Tests with coverage gates |
| `make check` | Everything above — must pass before any phase is done |

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the design, and the source of truth for it
- [`BUILD_PLAN.md`](BUILD_PLAN.md) — the phased build plan
- [`docs/adr/`](docs/adr/) — decision records
- [`CLAUDE.md`](CLAUDE.md) — working rules for this repository

## Status

Phase 0 of 12: the project foundation. The engine itself starts at Phase 2.
