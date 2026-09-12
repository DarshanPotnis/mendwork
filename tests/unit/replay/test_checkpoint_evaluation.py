"""Every checkpoint kind, passing and failing, against a scripted page."""

from typing import Any

import pytest
from pydantic import SecretStr, TypeAdapter

from mendwork.engine.domain.checkpoints import Checkpoint
from mendwork.engine.domain.enums import CheckpointKind
from mendwork.engine.errors import MendworkError
from mendwork.engine.ports.browser_types import (
    DownloadObservation,
    ElementRef,
    EqualsText,
    FieldExpectation,
    NonEmpty,
    ResponseObservation,
    WatchId,
    WatchKind,
)
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.safety.redaction import REDACTED
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.engine.verification.checkpoints import (
    CheckpointContext,
    CheckpointOutcome,
    evaluate_checkpoint,
    evaluation_order,
    watch_kinds,
)
from tests.fakes.browser import FakeBrowser, FakeElement, download
from tests.unit.replay.builders import CSS, TEST_ID, browser, selector

CHECKPOINT: TypeAdapter[Checkpoint] = TypeAdapter(Checkpoint)
START = 1000.0


def checkpoint(**raw: object) -> Checkpoint:
    return CHECKPOINT.validate_python(raw)


async def evaluate(
    page: FakeBrowser,
    raw: dict[str, Any],
    *,
    watch: WatchId | None = None,
    target: ElementRef | None = None,
    expectation: FieldExpectation | None = None,
    scrubber: SecretScrubber | None = None,
    run_ms: int = 60_000,
) -> CheckpointOutcome:
    context = CheckpointContext(
        browser=page,
        timer=page.timer,
        run_deadline=Deadline.after(page.timer, run_ms),
        default_timeout_ms=1_000,
        scrubber=scrubber or SecretScrubber(),
        watch=watch,
        target=target,
        expectation=expectation,
    )
    return await evaluate_checkpoint(context, 0, checkpoint(**raw))


def waited_ms(page: FakeBrowser) -> float:
    return round((page.timer.monotonic() - START) * 1000)


BANNER = {"kind": "no_error_banner"}
TEXT = {"kind": "text_present", "text": "14 shipments"}
DOWNLOAD = {"kind": "download_completed", "filename_pattern": r"report_\d+\.csv"}
RESPONSE = {
    "kind": "response_received",
    "mode": "prefix",
    "pattern": "https://api.example.test/",
    "status_min": 200,
    "status_max": 299,
}


def test_only_event_checkpoints_are_watched_before_the_action() -> None:
    checkpoints = [
        checkpoint(**TEXT),
        checkpoint(**DOWNLOAD),
        checkpoint(**RESPONSE),
        checkpoint(**BANNER),
    ]

    assert watch_kinds(checkpoints) == {WatchKind.DOWNLOAD, WatchKind.RESPONSE}
    assert watch_kinds([checkpoint(**TEXT)]) == frozenset()


def test_banners_run_last_and_everything_else_runs_as_written() -> None:
    checkpoints = [checkpoint(**BANNER), checkpoint(**TEXT), checkpoint(**DOWNLOAD)]

    assert [index for index, _ in evaluation_order(checkpoints)] == [1, 2, 0]


@pytest.mark.asyncio
async def test_url_matches_passes_with_the_url_as_detail() -> None:
    page = browser(url="https://portal.example.test/dashboard.html")

    outcome = await evaluate(
        page, {"kind": "url_matches", "mode": "regex", "pattern": r"https://[^/]+/dashboard\.html"}
    )

    assert (outcome.result.passed, outcome.result.detail) == (
        True,
        "https://portal.example.test/dashboard.html",
    )


@pytest.mark.asyncio
async def test_url_matches_fails_after_the_default_timeout() -> None:
    page = browser(url="https://portal.example.test/index.html")

    outcome = await evaluate(
        page,
        {"kind": "url_matches", "mode": "prefix", "pattern": "https://portal.example.test/app/"},
    )

    assert (outcome.result.passed, outcome.result.reason) == (False, "timeout")
    assert waited_ms(page) == 1_000


@pytest.mark.asyncio
async def test_a_checkpoint_timeout_overrides_the_default_and_the_run_deadline_caps_both() -> None:
    raw = {
        "kind": "url_matches",
        "mode": "prefix",
        "pattern": "https://elsewhere.test/",
        "timeout_ms": 250,
    }
    own = browser()
    capped = browser()

    await evaluate(own, raw)
    await evaluate(capped, raw, run_ms=100)

    assert (waited_ms(own), waited_ms(capped)) == (250, 100)


@pytest.mark.asyncio
async def test_element_visible_passes_when_the_page_shows_it_later() -> None:
    page = browser(elements={"heading": FakeElement(tag="h1", role="heading", name="Orders")})
    page.changes.append(lambda b: b.finds.update({CSS: "heading"}))

    outcome = await evaluate(
        page, {"kind": "element_visible", "selector": {"strategy": "css", "value": "#download-csv"}}
    )

    assert outcome.result.passed
    assert page.released


@pytest.mark.asyncio
async def test_element_visible_fails_when_the_element_never_shows() -> None:
    page = browser(finds={TEST_ID: (2,)})

    outcome = await evaluate(
        page,
        {"kind": "element_visible", "selector": {"strategy": "test_id", "value": "download-csv"}},
    )

    assert (outcome.result.passed, outcome.result.reason) == (False, "timeout")


@pytest.mark.asyncio
async def test_text_present_ignores_whitespace_but_not_case() -> None:
    page = browser(text="Summary:\n  14   shipments between Feb 10 and Apr 20.")

    assert (await evaluate(page, TEXT)).result.passed
    assert not (
        await evaluate(page, {"kind": "text_present", "text": "14 SHIPMENTS"})
    ).result.passed


@pytest.mark.asyncio
async def test_an_alert_with_nothing_to_say_is_not_an_error_banner() -> None:
    page = browser(alerts=("", "  \n "))

    assert (await evaluate(page, BANNER)).result.passed


@pytest.mark.asyncio
async def test_an_error_banner_fails_with_its_text_scrubbed() -> None:
    scrubber = SecretScrubber()
    scrubber.register(SecretStr("hunter2"))
    page = browser(alerts=("", "Password  hunter2 is wrong"))

    outcome = await evaluate(page, BANNER, scrubber=scrubber)

    assert (outcome.result.passed, outcome.result.reason) == (False, "error_banner_visible")
    assert outcome.result.detail == f"Password {REDACTED} is wrong"


@pytest.mark.asyncio
async def test_a_banner_selector_must_match_no_visible_element() -> None:
    raw = {"kind": "no_error_banner", "selector": {"strategy": "css", "value": ".flash--error"}}
    clean = browser()
    flagged = browser(finds={selector(strategy="css", value=".flash--error"): (2,)})

    assert (await evaluate(clean, raw)).result.passed
    failed = (await evaluate(flagged, raw)).result
    assert (failed.passed, failed.detail) == (
        False,
        "2 visible element(s) match the banner selector",
    )


@pytest.mark.asyncio
async def test_a_completed_download_with_a_matching_name_passes() -> None:
    page = browser()
    watch = await page.watch(frozenset({WatchKind.DOWNLOAD}))
    page.emit_download(download("report_2026.csv"))

    outcome = await evaluate(page, DOWNLOAD, watch=watch)

    assert (outcome.result.passed, outcome.result.detail) == (True, "report_2026.csv")
    assert outcome.download is not None


@pytest.mark.parametrize(
    ("observed", "reason"),
    [
        (download("report.pdf"), "filename_mismatch"),
        (
            DownloadObservation(suggested_filename="report_1.csv", failure="canceled"),
            "download_failed",
        ),
        (None, "timeout"),
    ],
)
@pytest.mark.asyncio
async def test_a_download_that_is_wrong_failed_or_absent_fails(
    observed: DownloadObservation | None, reason: str
) -> None:
    page = browser()
    watch = await page.watch(frozenset({WatchKind.DOWNLOAD}))
    if observed is not None:
        page.emit_download(observed)

    outcome = await evaluate(page, DOWNLOAD, watch=watch)

    assert (outcome.result.passed, outcome.result.reason, outcome.download) == (False, reason, None)


@pytest.mark.asyncio
async def test_an_event_checkpoint_without_a_watch_is_an_error() -> None:
    with pytest.raises(MendworkError, match="without watching"):
        await evaluate(browser(), DOWNLOAD)


@pytest.mark.asyncio
async def test_a_response_passes_only_inside_its_status_range() -> None:
    page = browser()
    watch = await page.watch(frozenset({WatchKind.RESPONSE}))
    page.emit_response(ResponseObservation(url="https://api.example.test/export", status=500))

    assert not (await evaluate(page, RESPONSE, watch=watch)).result.passed
    page.emit_response(ResponseObservation(url="https://api.example.test/export", status=201))
    passed = (await evaluate(page, RESPONSE, watch=watch)).result
    assert (passed.passed, passed.detail) == (True, "201 https://api.example.test/export")


@pytest.mark.parametrize(
    ("typed", "expectation", "passed", "reason"),
    [
        ("2026-02-10", EqualsText(value="2026-02-10"), True, None),
        ("2026-02-11", EqualsText(value="2026-02-10"), False, "value_differs"),
        ("s3cret", NonEmpty(), True, None),
        ("", NonEmpty(), False, "field_empty"),
    ],
)
@pytest.mark.asyncio
async def test_field_has_value_compares_plain_values_and_only_checks_secrets_are_there(
    typed: str, expectation: FieldExpectation, passed: bool, reason: str | None
) -> None:
    page = browser(
        elements={"field": FakeElement(tag="input", name="From", value=typed)},
        finds={TEST_ID: "field"},
    )
    match = await page.resolve_unique(TEST_ID)

    outcome = await evaluate(
        page, {"kind": "field_has_value"}, target=match.element, expectation=expectation
    )

    result = outcome.result
    assert (result.kind, result.passed, result.reason, result.detail) == (
        CheckpointKind.FIELD_HAS_VALUE,
        passed,
        reason,
        None,
    )


@pytest.mark.asyncio
async def test_field_has_value_needs_the_fill_it_checks() -> None:
    with pytest.raises(MendworkError, match="outside a fill step"):
        await evaluate(browser(), {"kind": "field_has_value"})
