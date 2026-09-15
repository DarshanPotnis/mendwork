"""Transient retry classification, backoff, and the navigate step's retry loop."""

import pytest
import structlog
from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.errors import NavigationError
from mendwork.engine.ports.browser_types import NavigationOutcome
from mendwork.engine.replay.config import RetryPolicy
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.navigation import navigate_with_retry
from mendwork.engine.replay.retry import backoff_delay_ms, is_transient
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser
from tests.fakes.ports import SequenceRandom
from tests.fakes.timer import FakeTimer

POLICY = RetryPolicy(
    max_attempts=3, initial_delay_ms=500, max_delay_ms=4_000, multiplier=2.0, jitter_ratio=0.5
)
URL = "https://portal.example.test/sign-in"


@pytest.mark.parametrize(
    ("reason", "status", "transient"),
    [
        ("timeout", None, True),
        ("ERR_CONNECTION_REFUSED", None, True),
        ("ERR_CONNECTION_RESET", None, True),
        ("ERR_NAME_RESOLUTION_FAILED", None, True),
        ("ERR_NAME_NOT_RESOLVED", None, False),
        ("ERR_CERT_AUTHORITY_INVALID", None, False),
        ("ERR_ABORTED", None, False),
        ("http_status", 502, True),
        ("http_status", 503, True),
        ("http_status", 504, True),
        ("http_status", 500, False),
        ("http_status", 404, False),
        ("navigation_failed", None, False),
    ],
)
def test_only_momentary_failures_are_transient(
    reason: str, status: int | None, transient: bool
) -> None:
    assert is_transient(reason, status) is transient


def test_backoff_grows_geometrically_up_to_the_cap() -> None:
    assert [backoff_delay_ms(attempt, POLICY, 0.0) for attempt in range(1, 7)] == [
        500, 1000, 2000, 4000, 4000, 4000,
    ]  # fmt: skip


def test_jitter_removes_at_most_its_ratio() -> None:
    assert backoff_delay_ms(1, POLICY, 0.999) == pytest.approx(250.25)


def test_invalid_backoff_arguments_are_refused() -> None:
    with pytest.raises(ValueError, match="starts at 1"):
        backoff_delay_ms(0, POLICY, 0.0)
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        backoff_delay_ms(1, POLICY, 1.0)


@given(st.integers(1, 200), st.floats(0.0, 1.0, exclude_max=True))
def test_every_delay_stays_within_its_jitter_band(attempt: int, unit: float) -> None:
    base = backoff_delay_ms(attempt, POLICY, 0.0)
    delay = backoff_delay_ms(attempt, POLICY, unit)

    assert base * (1 - POLICY.jitter_ratio) <= delay <= base <= POLICY.max_delay_ms


@given(st.integers(1, 199))
def test_the_base_delay_never_shrinks_as_attempts_grow(attempt: int) -> None:
    assert backoff_delay_ms(attempt, POLICY, 0.0) <= backoff_delay_ms(attempt + 1, POLICY, 0.0)


async def navigate(
    browser: FakeBrowser,
    *,
    policy: RetryPolicy = POLICY,
    deadline_ms: int = 60_000,
    unit: float = 0.5,
) -> object:
    timer = browser.timer
    return await navigate_with_retry(
        browser,
        URL,
        guard=None,
        policy=policy,
        navigation_timeout_ms=3_000,
        deadline=Deadline.after(timer, deadline_ms),
        timer=timer,
        randomness=SequenceRandom([unit]),
        scrubber=SecretScrubber(),
        log=structlog.stdlib.get_logger("test"),
    )


def refused() -> NavigationError:
    return NavigationError("the page could not be loaded", reason="ERR_CONNECTION_REFUSED")


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_after_a_jittered_pause() -> None:
    browser = FakeBrowser(
        timer=FakeTimer(), navigations=[refused(), NavigationOutcome(url=URL, status=200)]
    )

    report = await navigate(browser)

    assert report.attempts == 2  # type: ignore[attr-defined]
    assert browser.timer.pauses == [pytest.approx(0.375)]


@pytest.mark.asyncio
async def test_a_permanent_failure_is_not_retried() -> None:
    error = NavigationError("the page could not be loaded", reason="ERR_CERT_AUTHORITY_INVALID")
    browser = FakeBrowser(timer=FakeTimer(), navigations=[error])

    with pytest.raises(NavigationError) as caught:
        await navigate(browser)

    assert caught.value.context["attempts"] == 1
    assert browser.timer.pauses == []


@pytest.mark.asyncio
async def test_a_gateway_error_is_retried_and_a_missing_page_is_not() -> None:
    browser = FakeBrowser(
        timer=FakeTimer(),
        navigations=[
            NavigationOutcome(url=URL, status=503),
            NavigationOutcome(url=URL, status=404),
        ],
    )

    with pytest.raises(NavigationError) as caught:
        await navigate(browser)

    assert dict(caught.value.context) | {"url": URL} == {
        "reason": "http_status", "status": 404, "url": URL, "attempts": 2,
    }  # fmt: skip


@pytest.mark.asyncio
async def test_retries_stop_at_the_attempt_limit() -> None:
    browser = FakeBrowser(
        timer=FakeTimer(), navigations=[refused(), refused(), refused(), refused()]
    )

    with pytest.raises(NavigationError) as caught:
        await navigate(browser, unit=0.0)

    assert caught.value.context["attempts"] == 3
    assert browser.timer.pauses == [0.5, 1.0]
    assert len(browser.calls_named("navigate")) == 3


@pytest.mark.asyncio
async def test_no_retry_is_attempted_when_the_pause_would_outlast_the_deadline() -> None:
    browser = FakeBrowser(timer=FakeTimer(), navigations=[refused(), NavigationOutcome(url=URL)])

    with pytest.raises(NavigationError):
        await navigate(browser, deadline_ms=300, unit=0.0)

    assert browser.timer.pauses == []


def test_the_deadline_caps_timeouts_and_never_returns_zero() -> None:
    timer = FakeTimer()
    deadline = Deadline.after(timer, 1_500)

    assert deadline.cap(10_000) == 1_500
    timer.advance_ms(2_000)
    assert (
        deadline.expired,
        deadline.remaining_ms(),
        deadline.cap(10_000),
        deadline.timeout_ms(),
    ) == (True, 0, 1, 1)
    assert deadline.earliest(Deadline.after(timer, 5)).remaining_ms() == 0
