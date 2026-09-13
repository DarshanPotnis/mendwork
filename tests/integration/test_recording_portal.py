"""Recording the example tasks on the unmutated chaos portal, and replaying what was recorded.

Slow: each module-level recording signs in and walks the portal, and each replay does it
again in a fresh context. The golden file is rewritten with ``make recording-golden``.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from playwright.async_api import Browser

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.documents import workflow_document
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.runs import RunStatus
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.recording.selectors import SELECTOR, summarize
from tests.integration.portal import DEMO_PASSWORD
from tests.integration.recording_harness import RecordingOutcome, record_scripted
from tests.integration.recording_scripts import UNSCOPED_VIEW, ViewOrderDetail, download_report
from tests.integration.regenerate_recording_golden import GOLDEN
from tests.integration.replay_harness import ReplayOutcome, replay

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

CSV_HEADER = "shipment_id,ship_date,order_id,supplier,sku,quantity,amount_usd"


def comparable(version: WorkflowVersion) -> dict[str, Any]:
    """The workflow document without what depends on the machine or the moment."""
    # Any: a workflow document is arbitrary JSON-like data, edited here by key.
    document: dict[str, Any] = dict(workflow_document(version))
    document.pop("created_at")
    for step in document["steps"]:
        target = step.get("target")
        if isinstance(target, dict):
            target.pop("bbox", None)
    return document


async def replay_recording(
    browser: Browser, outcome: RecordingOutcome, workflow_id: str, directory: Path
) -> tuple[WorkflowVersion, ReplayOutcome]:
    decisions = outcome.decisions()
    version = outcome.workflow(workflow_id, decisions)
    inputs: dict[str, str] = {decision.name: decision.value for decision in decisions.inputs}
    result = await replay(browser, version, inputs, directory, secrets={"password": DEMO_PASSWORD})
    return version, result


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def download(browser: Browser, portal_url: str) -> AsyncIterator[RecordingOutcome]:
    yield await record_scripted(browser, f"{portal_url}index.html", download_report)


async def test_the_recording_matches_the_golden_file(download: RecordingOutcome) -> None:
    golden = WorkflowYamlCodec(max_bytes=1 << 20).decode(GOLDEN.read_bytes(), source=str(GOLDEN))

    assert comparable(download.workflow("download_report")) == comparable(golden), (
        "the golden recording is stale; regenerate it with `make recording-golden` and review "
        "the diff"
    )
    assert [(item.name, item.kind, item.description) for item in golden.inputs] == [
        ("start_url", "url", "URL of the page the workflow starts on (recorded at /index.html)"),
        ("email", "text", "Email address typed into the 'Email address' field"),
    ]


async def test_the_recording_replays_and_downloads_the_filtered_csv(
    browser: Browser, download: RecordingOutcome, tmp_path: Path
) -> None:
    version, outcome = await replay_recording(browser, download, "download_report", tmp_path)

    assert outcome.run.status is RunStatus.SUCCEEDED, outcome.run.error
    assert [step.action for step in version.steps].count(ActionType.FILL) == 4
    csv = outcome.step("click_download_csv").artifacts.download
    assert csv is not None
    lines = (outcome.run_directory / csv).read_text(encoding="utf-8").splitlines()
    assert lines[0] == CSV_HEADER
    assert len(lines) == 15


async def test_the_view_button_is_scoped_to_its_row_and_the_recording_replays(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    person = ViewOrderDetail(SELECTOR.validate_python(UNSCOPED_VIEW))

    recorded = await record_scripted(browser, f"{portal_url}index.html", person)

    view = recorded.recorded.steps[-1]
    assert view.description == "CLICK the 'View order PO-1042' button"
    assert view.target is not None
    summaries = [summarize(selector) for selector in view.target.selectors]
    assert (
        "role_name button 'View' (substring) within role_name row 'PO-1042' (substring)"
        in summaries
    )
    assert "role_name button 'View' (substring)" not in summaries
    assert person.unscoped_matches == 12
    _, outcome = await replay_recording(browser, recorded, "view_order_detail", tmp_path)
    assert outcome.run.status is RunStatus.SUCCEEDED, outcome.run.error
