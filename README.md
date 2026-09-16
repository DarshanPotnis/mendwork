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

## Measured

Every action Mendwork sends is checked against ground truth at the moment it happens — on the
chaos portal, the portal's own record of which element each control is, which the product never
reads; on a real application, a person's labels, written before Mendwork ran on that release. A step
that acted on an element that was not its control is a **wrong action**, and it is the number that
matters: automation that clicks the wrong thing is worse than automation that stops.

**The chaos grid.** Both example workflows at chaos levels 2, 3 and 5, on seeds 1000–1019 — fixed
before any benchmark ran and never used in development — through four systems, 120 runs each:

| System | Wrong-action steps | False successes | Changed steps completed | Unnecessary abstentions |
|---|---|---|---|---|
| Recorded CSS selector script | 3 of 393 (0.8%) | 0 | 114 of 194 (58.8%) | 80 of 390 (20.5%) |
| Role + name locator script | 0 of 414 (0.0%) | 0 | 140 of 228 (61.4%) | 88 of 413 (21.3%) |
| **Mendwork, free rungs only** | **0 of 619 (0.0%)** | **0** | **262 of 293 (89.4%)** | 31 of 607 (5.1%) |
| Mendwork + ground-truth chooser | 0 of 694 (0.0%) | 0 | 308 of 324 (95.1%) | 16 of 676 (2.4%) |

On the heal fixture suite's 67 one-change cases, Mendwork's free rungs completed **48 of 48**
changed steps, abstained correctly on **15 of 15** steps that must not act, and made **no
unnecessary abstentions and no wrong actions**.

The free rungs use **no model at all**: every heal above came from the free ladder, at a median
299 ms per healed step against 132 ms for a step that needed no healing. The ground-truth chooser is
not a model — it answers from ground truth, so its column is an upper bound on what the model rung's
rules allow, not a result.

**A real release pair.** A workflow recorded on a local Gitea 1.19.4 and replayed on 1.22.6, images
pinned by digest, with a person's labels for every step of the later release. Every system stopped at
the same step, and not because of how it found the element: the step's checkpoint, recorded on
1.19.4, asserts a heading that Gitea 1.22 deleted, so the step cannot be verified on the later
release however the element is found. No system reached the workflow's irreversible step. That
failure mode — a release removing the evidence that a step worked, rather than moving the control —
is one the chaos portal cannot produce, and it is the most useful thing the pair found.

**Reading these numbers.** Denominators differ because a system that stops early reaches fewer
steps. The script baselines check the workflow's own checkpoints, which a plain script usually
lacks, so they stop after a wrong action where a real script would carry on: their wrong-action
counts are a **lower bound**. The recorded-CSS baseline can only run a step whose element our
recorder gave a CSS selector, which it does only for elements with an id — a limit of our recorder,
not of CSS selectors. Two full runs of the grid produced **identical outcome digests across all 748
cells**, and CI re-runs 20 of them on every push, failing the build on a single wrong action.

Full numbers, every chart's underlying table, and the provenance of each run are in
[`benchmarks/results/scorecard.html`](benchmarks/results/scorecard.html); the method, the outcome
definitions and every limitation are in [ADR 0014](docs/adr/0014-benchmark-and-scorecard.md).

```sh
make bench     # the published benchmark, and the scorecard
```

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
make schema    # regenerate the workflow JSON Schema after changing the domain models
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
| `make schema` | Regenerate the workflow and benchmark-results JSON Schemas |
| `make bench` | Run the published benchmark and write the results and scorecard |

## Workflows

A workflow is a YAML file: declared inputs and secrets, then steps with ranked selectors,
a fingerprint of each target, and checkpoints. Editors that read
`schemas/workflow.schema.json` (VS Code with the recommended YAML extension) autocomplete
and check it as you type. Examples for the chaos portal live in
[`workflows/examples/`](workflows/examples/).

```sh
uv run mendwork validate workflows/examples/download_report.yaml
```

`validate` prints a one-line summary, or every problem as `file:line:column: path: message`
and exits 1.

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the design, and the source of truth for it
- [`BUILD_PLAN.md`](BUILD_PLAN.md) — the phased build plan
- [`docs/adr/`](docs/adr/) — decision records
- [`chaos-portal/README.md`](chaos-portal/README.md) — the demo target, its mutations, and the benchmark rules
- [`CLAUDE.md`](CLAUDE.md) — working rules for this repository

## Status

Phase 9 of 12, and the MVP is complete: record a workflow, replay it deterministically,
heal it when the site changes, verify every repair, save it as a new version, and measure
the whole thing against script baselines with ground truth at every action. The numbers
above are what that measurement produced.

Phases 10 to 12 — the multi-company API and worker, the dashboard, and deployment —
are not built.
