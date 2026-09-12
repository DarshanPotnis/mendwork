"""Evidence capture: what is stored, what is withheld, and what happens when capture fails."""

from collections.abc import Sequence
from pathlib import Path

import pytest
import structlog
from pydantic import SecretStr

from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.runs import TraceWithheld, TraceWithheldReason, parse_run_id
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import ArtifactStoreUnavailable, BrowserUnavailable, MendworkError
from mendwork.engine.ports.browser_types import (
    DownloadObservation,
    TraceExport,
    TraceNotSaved,
    TraceSaved,
)
from mendwork.engine.replay.artifact_names import TRACE
from mendwork.engine.replay.evidence import EvidenceRecorder
from mendwork.engine.safety.redaction import REDACTED
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser, download
from tests.fakes.ports import InMemoryArtifactStore
from tests.unit.replay.builders import CSS, TEST_ID, browser

pytestmark = pytest.mark.asyncio

SECRET = "hunter2-evidence"
RUN = parse_run_id("20260911T000000Z-00000001")


class BrokenBrowser(FakeBrowser):
    """A page that crashed: every capture fails, quoting a secret the page held."""

    async def screenshot(self, *, mask: Sequence[Selector], timeout_ms: int) -> bytes:
        raise BrowserUnavailable(f"page crashed while holding {SECRET}")

    async def dom_snapshot(self) -> str:
        raise BrowserUnavailable(f"page crashed while holding {SECRET}")

    async def export_trace(self, *, scrubber: SecretScrubber) -> TraceExport:
        raise BrowserUnavailable(f"page crashed while holding {SECRET}")


def recorder(
    page: FakeBrowser | None = None, artifacts: InMemoryArtifactStore | None = None
) -> tuple[EvidenceRecorder, FakeBrowser, InMemoryArtifactStore]:
    page = page or browser()
    store = artifacts or InMemoryArtifactStore()
    scrubber = SecretScrubber()
    scrubber.register(SecretStr(SECRET))
    evidence = EvidenceRecorder(
        browser=page,
        artifacts=store,
        run_id=RUN,
        scrubber=scrubber,
        timeout_ms=1_000,
        log=structlog.stdlib.get_logger("test"),
    )
    return evidence, page, store


def broken() -> BrokenBrowser:
    return BrokenBrowser(timer=browser().timer)


async def test_a_screenshot_is_stored_under_the_steps_name() -> None:
    evidence, _, store = recorder()
    problems: list[str] = []

    name = await evidence.screenshot(2, StepId("fill_password"), problems, fatal=True)

    assert name == "steps/003_fill_password.png"
    assert store.names(RUN) == ["steps/003_fill_password.png"]
    assert problems == []


async def test_a_store_that_cannot_write_stops_a_succeeding_step() -> None:
    evidence, _, _ = recorder(artifacts=InMemoryArtifactStore(fail=True))

    with pytest.raises(ArtifactStoreUnavailable):
        await evidence.screenshot(0, StepId("open"), [], fatal=True)


async def test_a_store_that_cannot_write_is_only_noted_while_recording_a_failure() -> None:
    evidence, _, _ = recorder(artifacts=InMemoryArtifactStore(fail=True))
    problems: list[str] = []

    assert await evidence.screenshot(0, StepId("open"), problems, fatal=False) is None
    assert problems == ["screenshot: ArtifactStoreUnavailable: the store is failing on purpose"]


async def test_a_browser_that_cannot_capture_is_noted_with_its_message_scrubbed() -> None:
    evidence, _, _ = recorder(page=broken())
    problems: list[str] = []

    assert await evidence.screenshot(0, StepId("open"), problems, fatal=True) is None
    assert await evidence.dom_snapshot(0, StepId("open"), problems) is None
    assert await evidence.trace(problems) == (None, None)
    assert problems == [
        f"screenshot: BrowserUnavailable: page crashed while holding {REDACTED}",
        f"dom snapshot: BrowserUnavailable: page crashed while holding {REDACTED}",
        f"trace: BrowserUnavailable: page crashed while holding {REDACTED}",
    ]


async def test_a_dom_snapshot_is_stored_scrubbed() -> None:
    page = browser()
    page.html = f"<input value='{SECRET}'>"
    evidence, _, store = recorder(page=page)

    name = await evidence.dom_snapshot(7, StepId("apply_filter"), [])

    assert name == "failure/008_apply_filter.dom.html"
    assert store.files[(RUN, name)].decode() == f"<input value='{REDACTED}'>"


async def test_a_disabled_trace_records_nothing() -> None:
    evidence, _, store = recorder()

    assert await evidence.trace([]) == (None, None)
    assert store.names(RUN) == []


async def test_a_saved_trace_is_moved_into_the_run() -> None:
    page = browser()
    page.trace = TraceSaved(path=Path("/browser/work/trace.zip"))
    evidence, _, store = recorder(page=page)

    assert await evidence.trace([]) == (TRACE, None)
    assert store.adopted[(RUN, TRACE)] == Path("/browser/work/trace.zip")


async def test_a_trace_withheld_on_a_secret_page_names_the_step_that_typed_it() -> None:
    page = browser()
    page.trace = TraceNotSaved(reason=TraceWithheldReason.SECRET_BEARING_PAGE)
    evidence, _, _ = recorder(page=page)
    evidence.secret_typed(2, StepId("fill_password"), TEST_ID)

    _, withheld = await evidence.trace([])

    assert withheld == TraceWithheld(
        reason=TraceWithheldReason.SECRET_BEARING_PAGE,
        typed_at_index=2,
        typed_at_step=StepId("fill_password"),
    )


@pytest.mark.parametrize(
    ("reason", "typed"),
    [(TraceWithheldReason.SECRET_DETECTED, True), (TraceWithheldReason.SECRET_BEARING_PAGE, False)],
)
async def test_a_withheld_trace_names_no_step_when_none_applies(
    reason: TraceWithheldReason, typed: bool
) -> None:
    page = browser()
    page.trace = TraceNotSaved(reason=reason)
    evidence, _, _ = recorder(page=page)
    if typed:
        evidence.secret_typed(2, StepId("fill_password"), TEST_ID)

    assert await evidence.trace([]) == (None, TraceWithheld(reason=reason))


async def test_every_field_typed_from_a_secret_is_masked_once() -> None:
    evidence, page, _ = recorder()
    evidence.secret_typed(1, StepId("fill_password"), TEST_ID)
    evidence.secret_typed(4, StepId("fill_token"), CSS)
    evidence.secret_typed(5, StepId("fill_password_again"), TEST_ID)

    await evidence.screenshot(6, StepId("sign_in"), [], fatal=True)

    assert page.masks == [(TEST_ID, CSS)]


async def test_downloads_keep_their_name_and_a_repeated_name_is_prefixed_with_its_step() -> None:
    evidence, _, store = recorder()

    first = await evidence.keep_download(download("report.csv", "/browser/1"), 3, StepId("export"))
    second = await evidence.keep_download(download("report.csv", "/browser/2"), 5, StepId("again"))

    assert (first, second) == ("downloads/report.csv", "downloads/006_again_report.csv")
    assert store.adopted[(RUN, second)] == Path("/browser/2")


async def test_only_a_completed_download_can_be_kept() -> None:
    evidence, _, _ = recorder()

    with pytest.raises(MendworkError, match="completed download"):
        await evidence.keep_download(
            DownloadObservation(suggested_filename="x.csv", failure="canceled"), 0, StepId("export")
        )
