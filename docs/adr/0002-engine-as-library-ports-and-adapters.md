# 2. The engine is a library, behind ports and adapters

- **Status:** Accepted
- **Date:** 2026-09-10

## Context

Mendwork's value is its decision-making: which element a step means, when a repair is
trustworthy, when to stop and ask a human. That logic has to be tested exhaustively,
because a wrong decision clicks a real button on a real site — the headline metric is a
wrong-action rate of zero.

Everything else — Playwright, Postgres, FastAPI, model provider HTTP clients, the
filesystem — is replaceable plumbing. If the decision logic is entangled with that
plumbing, then testing a scoring rule needs a browser, a database, and a network, which
makes tests slow and flaky, and makes swapping any one dependency a rewrite. Several
dependencies are already known to change: storage moves from the filesystem to Postgres
in Phase 10, and model providers are explicitly meant to be swapped per customer.

## Decision

`mendwork.engine` is a plain Python library. It imports only the standard library,
Pydantic, rapidfuzz, structlog, and other engine modules.

It reaches the outside world exclusively through **ports** — `typing.Protocol`
interfaces that the engine itself owns and defines, in `engine/ports`: `BrowserPort`,
`ModelPort`, `WorkflowStore`, `ArtifactStore`, `EventSink`, `SecretResolver`, `Clock`.

`mendwork.adapters` provides concrete implementations of those ports and may import
whatever a vendor SDK requires. `mendwork.apps` (CLI, API, worker) are composition
roots: they construct adapters, inject them into the engine through constructors, and
contain no business logic.

Dependencies are injected through constructors. There is no module-level mutable state,
no singleton, and no hidden global — including configuration: `Settings` lives outside
the engine, and apps pass the values the engine needs as ordinary arguments.

`import-linter` enforces the boundary in CI with four contracts: the engine may not
import adapters, apps, `mendwork.settings`, or Playwright, httpx, FastAPI, or SQLAlchemy;
and adapters may not import apps.

## Alternatives considered

- **A layered application with no enforced boundary.** Conventional and quick to write.
  Boundaries that are only documented erode under deadline pressure, and the erosion is
  invisible until tests need a browser to run. Rejected: the contracts cost one CI step
  and fail loudly the first time someone crosses the line.
- **A multi-package workspace** (separate distributions for engine, adapters, apps).
  It enforces the same boundary through packaging itself. It also adds version
  resolution, release coordination, and editable-install friction across packages, for
  safety `import-linter` already provides inside one package. Rejected as overhead
  without added protection. Recorded in `ARCHITECTURE.md` section 4.
- **Abstract base classes instead of Protocols.** ABCs give runtime enforcement, but they
  force adapters to inherit from an engine class, which inverts the dependency we are
  trying to protect and makes test fakes heavier. Protocols are structural, so a fake is
  just a small class with the right methods. Rejected.

## Consequences

- The engine's tests need no network, no browser, and no database; they run against the
  in-memory fakes in `tests/fakes/`. This is what makes a 90% engine coverage gate and
  deterministic, seed-fixed tests achievable.
- Swapping an implementation — filesystem storage for Postgres, one model provider for
  another — is a change in `adapters/` and one line of wiring in an app.
- Adding a capability costs more up front: a port, an adapter, a fake, and wiring, rather
  than a direct call. This is the price of the boundary and is accepted.
- The engine cannot read configuration or the clock for itself. Callers must pass
  thresholds, weights, and a `Clock`, which is exactly what makes engine behaviour
  reproducible in a test.
