# 5. Async Playwright test harness

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

From Phase 1, `make check` drives a real Chromium against the chaos portal. The harness
has three jobs:

- **Stay fast:** a browser launch per test would add minutes.
- **Keep tests isolated:** no cookies, storage, or event listeners may leak between them.
- **Pass under the existing strictness, not by relaxing it:**
  - pytest-asyncio strict mode;
  - `filterwarnings = ["error"]`;
  - `mypy --strict` over tests.

From Phase 3, the product's `BrowserPort` adapter uses Playwright's async API. CLAUDE.md
requires async for all I/O, and its integration tests will build on this harness.

## Decision

- **Async API, no pytest-playwright.** Tests use `playwright.async_api` directly.
  pytest-playwright's fixtures are built on the sync API, and a second plugin would add a
  dependency for fixtures that take a dozen lines to write.
- **Fixture scopes:**

  | Fixture | Scope | Loop scope |
  |---|---|---|
  | `portal_url` (the same `PortalServer` that `make portal` uses, on a thread) | session | none (synchronous) |
  | `playwright_instance`, `browser` | session | session |
  | `portal` (a fresh context: 1280×720, downloads on, 5 s default timeout) | function | session |

- **One session event loop for browser tests.**
  - Playwright's driver connection belongs to the loop that created it, so a
    session-scoped browser must be created, used, and closed on one loop.
  - Every browser test module declares
    `pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]`.
  - The global default fixture loop scope stays `function` for everything else.
- **One exception to the fresh-context rule: the heal pair sweep.**
  - Its 117 pair tests share one module-scoped context and sign in again only after a pair
    signs out. A context and sign-in per pair would add several seconds for no isolation
    gain.
  - Every load passes its own seed and `only=`, which replaces any stored chaos
    configuration, and heal mutations create no wrong actions to leak.
- **Teardown order:** context, then browser, then the Playwright driver, all while the
  session loop is still running, then the server thread. Closing in this order is what
  keeps unclosed-transport and ResourceWarnings from appearing, and none are filtered.
- **Errors fail loudly.** The driver records every page error and console error, and any
  wait for readiness fails if one occurred.
- **Navigations must be proven.** A navigation triggered by an action is awaited through
  `expect_page`, which marks the current document first. A page's link back to itself is
  still proven to load a new document.
- **`tests/` is a package**, with `__init__.py` files. Two `conftest.py` files would
  otherwise collide as duplicate modules under mypy, and shared helpers
  (`tests.integration.portal`, `tests.integration.probes`) import cleanly.

## Alternatives considered

- **Sync API with pytest-playwright.** Least code today, but Phase 3's adapter is async.
  Integration tests would then mix two Playwright APIs, or be rewritten. Rejected.
- **Function-scoped event loop, browser per test.** Maximum isolation, but launching
  Chromium per test multiplies the suite's run time. Rejected: a fresh context per test
  already isolates cookies, storage, and listeners.
- **Session loop for every async test globally**
  (`asyncio_default_test_loop_scope = "session"`). One line of config, but it would
  silently share a loop with future engine unit tests that should not. Rejected in favour
  of an explicit per-module mark.

## Consequences

- The browser suite, including the full heal pair sweep, stays under a minute with one
  Chromium process.
- Every new browser test module must carry the session-loop mark. A missing mark fails
  loudly with a different-loop error rather than passing by accident.
- Tests can only use loop-bound objects created on the session loop, which is the same
  constraint the Phase 3 adapter will have.

## Amendment (2026-09-15): a session is one pytest process

Tests now run in four pytest-xdist workers (ADR 0012). Each worker is its own pytest process
with its own session, so every decision above holds per worker rather than once per run:

- **Fixture scopes are unchanged, and no fixture code changed.** Each worker starts its own
  `PortalServer` for `portal_url` (bound to port 0, so the operating system gives every worker a
  different port; the module-scoped fixture sites do the same), its own Playwright driver and
  Chromium, and its own session event loop. No browser, loop, server, or context is shared across
  workers.
- **"One Chromium process" becomes one per worker.** Memory grows with the worker count, which is
  one reason the count is four on an 8 GB machine (ADR 0012).
- **Teardown order** (context, browser, driver, server thread) applies inside each worker, which
  runs it when its own session ends.
- **The heal pair sweep's shared context stays safe.** Tests are distributed with
  `--dist loadfile`, so every test of a module runs on one worker, in file order, and a
  module-scoped fixture is created exactly once, as in a serial run.
- **The session-loop mark is still required per module;** each worker creates its session loop
  the same way the serial run does.
