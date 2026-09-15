"""Closing Chromium tolerates a driver or browser that is already gone, and nothing else."""

import pytest
from playwright.async_api import Error as PlaywrightError
from structlog.testing import capture_logs

from mendwork.adapters.browser_playwright.errors import is_closed
from mendwork.adapters.browser_playwright.launcher import close_chromium

pytestmark = pytest.mark.asyncio

DRIVER_GONE = "Browser.close: Connection closed while reading from the driver"


class Browser:
    """A browser whose close does what the test says."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        if self.error is not None:
            raise self.error


class Driver:
    """Playwright's driver connection, whose stop does what the test says."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True
        if self.error is not None:
            raise self.error


async def test_a_driver_that_already_exited_is_logged_and_both_closes_are_still_attempted() -> None:
    browser = Browser(Exception(DRIVER_GONE))
    driver = Driver(Exception("Connection closed while reading from the driver"))

    with capture_logs() as logs:
        await close_chromium(browser, driver)

    assert (browser.closed, driver.stopped) == (True, True)
    assert [(entry["event"], entry["log_level"], entry["closing"]) for entry in logs] == [
        ("chromium_already_closed", "debug", "browser"),
        ("chromium_already_closed", "debug", "driver"),
    ]
    assert logs[0]["detail"] == DRIVER_GONE


async def test_a_browser_playwright_reports_as_closed_is_tolerated() -> None:
    driver = Driver()

    await close_chromium(
        Browser(PlaywrightError("Target page, context or browser has been closed")), driver
    )

    assert driver.stopped


@pytest.mark.parametrize(
    "error",
    [Exception("disk full"), PlaywrightError("Protocol error (Browser.close): internal")],
)
async def test_any_other_failure_to_close_the_browser_is_raised(error: Exception) -> None:
    with pytest.raises(type(error)) as raised:
        await close_chromium(Browser(error), Driver())

    assert raised.value is error


async def test_any_other_failure_to_stop_the_driver_is_raised() -> None:
    browser = Browser()

    with pytest.raises(RuntimeError, match="driver would not stop"):
        await close_chromium(browser, Driver(RuntimeError("driver would not stop")))

    assert browser.closed


async def test_nothing_launched_closes_nothing() -> None:
    with capture_logs() as logs:
        await close_chromium(None, None)

    assert logs == []


async def test_a_plain_exception_counts_as_closed_only_when_it_says_so() -> None:
    assert is_closed(Exception(DRIVER_GONE))
    assert is_closed(PlaywrightError("Browser has been closed"))
    assert not is_closed(Exception("disk full"))
    assert not is_closed(PlaywrightError("Timeout 5000ms exceeded"))
