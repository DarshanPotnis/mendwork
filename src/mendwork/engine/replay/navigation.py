"""Navigate steps: check the egress policy, load a URL, and retry only transient failures."""

import structlog

from mendwork.engine.domain.runs import NavigationReport
from mendwork.engine.errors import NavigationError
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.config import RetryPolicy
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.navigation_guard import NavigationGuard
from mendwork.engine.replay.retry import HTTP_STATUS_REASON, backoff_delay_ms, is_transient
from mendwork.engine.safety.secret_scrub import SecretScrubber

_FIRST_ERROR_STATUS = 400


async def navigate_with_retry(
    browser: BrowserPort,
    url: str,
    *,
    guard: NavigationGuard | None,
    policy: RetryPolicy,
    navigation_timeout_ms: int,
    deadline: Deadline,
    timer: Timer,
    randomness: RandomSource,
    scrubber: SecretScrubber,
    log: structlog.stdlib.BoundLogger,
) -> NavigationReport:
    """Load a URL. A final main-document status of 400 or more fails the step.

    With a guard, the URL is checked against the run's egress policy before every attempt, and
    a refusal (EgressBlocked) is never retried. Only the recorder passes no guard: a person drives
    its browser, and the recording's verification replay is held to the policy. Transient
    failures (timeouts, dropped connections, 502/503/504) are retried with backoff up to the
    policy's attempts, and never past the run's deadline.
    """
    attempt = 1
    while True:
        try:
            if guard is not None:
                await guard.check(url, timeout_ms=deadline.cap(navigation_timeout_ms))
            outcome = await browser.navigate(url, timeout_ms=deadline.cap(navigation_timeout_ms))
            if outcome.status is not None and outcome.status >= _FIRST_ERROR_STATUS:
                raise NavigationError(
                    f"the page answered with HTTP status {outcome.status}",
                    reason=HTTP_STATUS_REASON,
                    status=outcome.status,
                    url=scrubber.scrub_text(outcome.url),
                )
            return NavigationReport(
                url=scrubber.scrub_text(outcome.url), status=outcome.status, attempts=attempt
            )
        except NavigationError as error:
            reason = error.context.get("reason")
            status = error.context.get("status")
            delay_ms = backoff_delay_ms(attempt, policy, randomness.unit())
            retry = (
                attempt < policy.max_attempts
                and is_transient(reason, status)
                and delay_ms < deadline.remaining_ms()
            )
            if not retry:
                raise NavigationError(
                    error.message, **{**error.context, "attempts": attempt}
                ) from error
            log.warning(
                "navigation_retry",
                attempt=attempt,
                reason=str(reason),
                status=status,
                delay_ms=round(delay_ms),
            )
            await timer.pause(delay_ms / 1000)
            attempt += 1
