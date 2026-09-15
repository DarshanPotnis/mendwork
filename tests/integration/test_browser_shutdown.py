"""Tearing Chromium down after Playwright's driver has already exited (ADR 0012's shutdown finding).

A terminal's Ctrl+C reaches Playwright's driver as well as Mendwork, because the driver runs in the
command's process group. On a busy machine the driver can exit before a run's session is torn down,
or before the launcher closes the browser. By then the run's record is final (or, for an interrupt,
about to be written from the engine's own rules), so the command must exit with the code that
record gives: never an infrastructure failure from the teardown, never a traceback. A recording's
browser must close cleanly too.

Each test kills its own driver and waits for the exit event at a chosen moment, just before the
session is torn down or just before the launcher closes, so the order is forced rather than left to
timing.
"""

import asyncio
import json
import os
import signal
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Final

import pytest
from structlog.testing import capture_logs
from typer.testing import CliRunner, Result

from benchmarks.chaos.local_egress import local_policy
from mendwork.adapters.browser_playwright.launcher import (
    ChromiumLauncher,
    EgressEnforcement,
    LaunchOptions,
    SessionOptions,
)
from mendwork.adapters.browser_playwright.recording.channel import LoggingInbound
from mendwork.adapters.browser_playwright.recording.launcher import (
    ChromiumRecordingLauncher,
    RecordingOptions,
)
from mendwork.adapters.browser_playwright.recording.session import PlaywrightRecordingSession
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.exit_codes import exit_code_for
from mendwork.apps.cli.main import app
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.domain.documents import parse_workflow_document
from mendwork.engine.domain.runs import Run, RunId, RunStatus, StepStatus
from mendwork.engine.ports.browser_types import ElementRef
from mendwork.engine.safety.egress import EgressPolicy
from tests.processes import playwright_drivers, still_running
from tests.workflows import REPO_ROOT, Document

pytestmark = [pytest.mark.browser, pytest.mark.slow]

FIXTURE_SITE: Final = REPO_ROOT / "tests" / "fixtures" / "sites" / "secret_login"
EXIT_TIMEOUT_S: Final = 30
NEVER_MS: Final = 30_000
"""How long the export's checkpoint waits for text the page never shows: longer than any test."""
ALREADY_CLOSED: Final = "chromium_already_closed"


class Driver:
    """The one Playwright driver a launcher started, found as it starts and ended on request."""

    def __init__(self) -> None:
        self.pids: set[int] = set()
        self.ended = False

    async def find(self, before: set[int]) -> None:
        self.pids |= await playwright_drivers() - before

    async def end(self) -> None:
        """Kill the driver and wait until the operating system reports it gone; once only."""
        if self.ended:
            return
        assert len(self.pids) == 1, f"expected this launcher's one driver, found {self.pids}"
        for pid in self.pids:
            os.kill(pid, signal.SIGKILL)
        assert await asyncio.to_thread(still_running, self.pids, EXIT_TIMEOUT_S) == set()
        self.ended = True


class DriverExitsFirst(ChromiumLauncher):
    """A run's launcher whose Playwright driver is gone before Chromium is torn down."""

    INTERRUPT_WHEN_OPEN: ClassVar[bool] = False
    """Send this process SIGINT as the session opens, as a first Ctrl+C would."""
    END_BEFORE_SESSION_CLOSES: ClassVar[bool] = False
    """End the driver before the session is torn down, not only before the launcher closes."""

    def __init__(
        self, launch: LaunchOptions, options: SessionOptions, enforcement: EgressEnforcement
    ) -> None:
        super().__init__(launch, options, enforcement)
        self.driver = Driver()

    @asynccontextmanager
    async def session(
        self, run_id: RunId, egress: EgressPolicy
    ) -> AsyncIterator[PlaywrightSession]:
        before = await playwright_drivers()
        async with AsyncExitStack() as stack:
            session = await stack.enter_async_context(super().session(run_id, egress))
            await self.driver.find(before)
            if self.END_BEFORE_SESSION_CLOSES:
                # Exit callbacks run last-in first-out: the driver ends before the session's own
                # teardown, however the run leaves the session.
                stack.push_async_callback(self.driver.end)
            if self.INTERRUPT_WHEN_OPEN:
                os.kill(os.getpid(), signal.SIGINT)
            yield session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.driver.end()
        await super().__aexit__(exc_type, exc_value, traceback)


class InterruptedThenDriverExits(DriverExitsFirst):
    """Interrupted as the session opens; the driver is gone before the launcher closes."""

    INTERRUPT_WHEN_OPEN: ClassVar[bool] = True


class InterruptedThenDriverExitsBeforeTeardown(DriverExitsFirst):
    """Interrupted as the session opens; the driver is gone before the session is torn down."""

    INTERRUPT_WHEN_OPEN: ClassVar[bool] = True
    END_BEFORE_SESSION_CLOSES: ClassVar[bool] = True


class DriverExitsBeforeTeardown(DriverExitsFirst):
    """The driver is gone before the session is torn down; the run interrupts itself elsewhere."""

    END_BEFORE_SESSION_CLOSES: ClassVar[bool] = True


class RecordingDriverExitsFirst(ChromiumRecordingLauncher):
    """A recording's launcher whose driver is gone before Chromium is torn down."""

    END_BEFORE_SESSION_CLOSES: ClassVar[bool] = False

    def __init__(
        self, launch: LaunchOptions, options: RecordingOptions, inbound: LoggingInbound
    ) -> None:
        super().__init__(launch, options, inbound)
        self.driver = Driver()

    @asynccontextmanager
    async def recording_session(self) -> AsyncIterator[PlaywrightRecordingSession]:
        before = await playwright_drivers()
        async with AsyncExitStack() as stack:
            session = await stack.enter_async_context(super().recording_session())
            await self.driver.find(before)
            if self.END_BEFORE_SESSION_CLOSES:
                stack.push_async_callback(self.driver.end)
            yield session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.driver.end()
        await super().__aexit__(exc_type, exc_value, traceback)


class RecordingDriverExitsBeforeTeardown(RecordingDriverExitsFirst):
    """The same, with the driver gone before the recording session is torn down."""

    END_BEFORE_SESSION_CLOSES: ClassVar[bool] = True


@pytest.fixture(scope="module")
def fixture_site() -> Iterator[str]:
    with PortalServer(FIXTURE_SITE, host="127.0.0.1", port=0) as server:
        yield server.url


def open_page_workflow() -> Document:
    """Open the signed-in page and check that its Export button is there."""
    return {
        "schema_version": 1,
        "workflow_id": "shutdown_probe",
        "version": 1,
        "created_at": "2026-09-15T00:00:00Z",
        "inputs": [{"name": "site_url", "kind": "url"}],
        "steps": [
            {
                "id": "open",
                "intent": "Open the signed-in page",
                "action": "navigate",
                "risk": "safe",
                "value": {"kind": "input", "name": "site_url"},
                "checkpoints": [
                    {
                        "kind": "element_visible",
                        "selector": {"strategy": "test_id", "value": "export"},
                    }
                ],
            }
        ],
    }


def irreversible_export_workflow() -> Document:
    """Open the signed-in page, click Export irreversibly, and wait for text that never comes."""
    opening = open_page_workflow()
    export = {
        "id": "export",
        "intent": "Click the 'Export' button",
        "action": "click",
        "risk": "irreversible",
        "target": {
            "tag": "button",
            "role": "button",
            "accessible_name": "Export",
            "attributes": {"id": "export", "type": "button"},
            "structural_path": "main > button",
            "selectors": [{"strategy": "test_id", "value": "export"}],
        },
        "checkpoints": [
            {"kind": "text_present", "text": "Export complete", "timeout_ms": NEVER_MS}
        ],
    }
    steps = opening["steps"]
    assert isinstance(steps, list)
    return {**opening, "workflow_id": "shutdown_irreversible_probe", "steps": [*steps, export]}


def interrupt_after_every_click(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send this process SIGINT as soon as a click has reached the page, as a Ctrl+C then would."""
    click = PlaywrightSession.click

    async def clicked(session: PlaywrightSession, element: ElementRef, *, timeout_ms: int) -> None:
        await click(session, element, timeout_ms=timeout_ms)
        os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(PlaywrightSession, "click", clicked)


def run_with(
    launcher: type[ChromiumLauncher],
    workflow_document: Document,
    site: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Result, Run]:
    workflow = tmp_path / "shutdown_probe.yaml"
    workflow.write_bytes(
        WorkflowYamlCodec(max_bytes=1 << 20).encode(parse_workflow_document(workflow_document))
    )
    artifacts = tmp_path / "artifacts"
    origins = [exception.origin for exception in local_policy([site]).loopback_exceptions]
    monkeypatch.setattr("mendwork.apps.cli.run.ChromiumLauncher", launcher)
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(workflow),
            "--artifacts-dir",
            str(artifacts),
            "--store-dir",
            str(tmp_path / "workflow-store"),
            "--input",
            f"site_url={site}app.html",
        ],
        env={
            "MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS": json.dumps(origins),
            "MENDWORK_LOG_LEVEL": "DEBUG",
        },
    )
    (run_directory,) = (artifacts / "runs").iterdir()
    return result, Run.model_validate_json((run_directory / "run.json").read_bytes())


def test_a_finished_run_exits_with_its_records_code_when_the_driver_is_already_gone(
    fixture_site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, run = run_with(
        DriverExitsFirst, open_page_workflow(), fixture_site, tmp_path, monkeypatch
    )

    assert run.status is RunStatus.SUCCEEDED
    assert result.exit_code == exit_code_for(run) == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert ALREADY_CLOSED in result.stderr


@pytest.mark.parametrize(
    "launcher", [InterruptedThenDriverExits, InterruptedThenDriverExitsBeforeTeardown]
)
def test_an_interrupted_run_exits_130_from_its_record_when_the_driver_is_already_gone(
    launcher: type[ChromiumLauncher],
    fixture_site: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, run = run_with(launcher, open_page_workflow(), fixture_site, tmp_path, monkeypatch)

    assert run.status is RunStatus.CANCELLED
    assert result.exit_code == exit_code_for(run) == 130, result.stderr
    assert "Traceback" not in result.stderr
    assert ALREADY_CLOSED in result.stderr


def test_an_interrupted_irreversible_click_still_needs_review_when_the_driver_is_gone(
    fixture_site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interrupt_after_every_click(monkeypatch)

    result, run = run_with(
        DriverExitsBeforeTeardown,
        irreversible_export_workflow(),
        fixture_site,
        tmp_path,
        monkeypatch,
    )

    assert [item.step_id for item in run.irreversible_dispatched] == ["export"]
    assert run.status is RunStatus.NEEDS_REVIEW, run.error
    assert [step.status for step in run.steps] == [StepStatus.SUCCEEDED, StepStatus.CANCELLED]
    assert result.exit_code == exit_code_for(run) == 4, result.stderr
    assert "Traceback" not in result.stderr
    assert ALREADY_CLOSED in result.stderr


@pytest.mark.parametrize(
    "launcher", [RecordingDriverExitsFirst, RecordingDriverExitsBeforeTeardown]
)
@pytest.mark.asyncio
async def test_a_recording_browser_closes_cleanly_when_its_driver_is_already_gone(
    launcher: type[RecordingDriverExitsFirst],
) -> None:
    recording = launcher(
        LaunchOptions(headless=True, slow_mo_ms=0),
        RecordingOptions(viewport_width=1280, viewport_height=720, default_timeout_ms=5_000),
        LoggingInbound(),
    )

    with capture_logs() as logs:
        async with recording, recording.recording_session():
            pass

    assert ALREADY_CLOSED in [entry["event"] for entry in logs]
