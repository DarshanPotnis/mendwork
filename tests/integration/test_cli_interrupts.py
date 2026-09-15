"""Ctrl+C on a real ``mendwork run`` process (ADR 0011).

Each test starts the CLI in a process group of its own against the JS-free fixture site, reads its
JSON event stream, and sends SIGINT to the whole group at a chosen event, as a terminal's Ctrl+C
does. The interrupt lands at a known point without waiting on time. The record the run leaves must
be consistent (cancelled, or needing review once an irreversible action was dispatched), and no
Chromium process may outlive the command.
"""

import asyncio
import json
import os
import select
import signal
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from benchmarks.chaos.local_egress import local_policy
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.domain.documents import parse_workflow_document
from mendwork.engine.domain.runs import Run, RunStatus, StepStatus
from tests.workflows import REPO_ROOT, Document

pytestmark = [pytest.mark.browser, pytest.mark.slow]

FIXTURE_SITE: Final = REPO_ROOT / "tests" / "fixtures" / "sites" / "secret_login"
MENDWORK: Final = Path(sys.executable).with_name("mendwork")
EVENT_TIMEOUT_S: Final = 60
EXIT_TIMEOUT_S: Final = 30
NEVER_MS: Final = 60_000
"""How long the export's checkpoint waits for text the page never shows: longer than any test."""


@pytest.fixture(scope="module")
def fixture_site() -> Iterator[str]:
    with PortalServer(FIXTURE_SITE, host="127.0.0.1", port=0) as server:
        yield server.url


def export_workflow(risk: str) -> Document:
    """Open the signed-in page, then click Export and wait for a confirmation that never comes."""
    return {
        "schema_version": 1,
        "workflow_id": "interrupt_probe",
        "version": 1,
        "created_at": "2026-09-14T00:00:00Z",
        "inputs": [{"name": "site_url", "kind": "url"}],
        "steps": [
            {
                "id": "open",
                "intent": "Open the signed-in page",
                "action": "navigate",
                "risk": "safe",
                "value": {"kind": "input", "name": "site_url"},
            },
            {
                "id": "export",
                "intent": "Click the 'Export' button",
                "action": "click",
                "risk": risk,
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
            },
        ],
    }


@dataclass(frozen=True)
class Interrupted:
    """What an interrupted command left: its exit code, output, record, and processes."""

    code: int
    events: list[dict[str, object]]
    result: dict[str, object] | None
    stderr: str
    run: Run
    outlived: set[int]
    """Processes the command started that were still running after it exited."""


async def interrupt(site: str, tmp_path: Path, *, risk: str, signals: int) -> Interrupted:
    """Run the export workflow and send ``signals`` SIGINTs once the export's action is sent."""
    workflow = tmp_path / "interrupt_probe.yaml"
    workflow.write_bytes(
        WorkflowYamlCodec(max_bytes=1 << 20).encode(parse_workflow_document(export_workflow(risk)))
    )
    artifacts = tmp_path / "artifacts"
    origins = [exception.origin for exception in local_policy([site]).loopback_exceptions]
    env = {
        **os.environ,
        "MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS": json.dumps(origins),
        "MENDWORK_LOG_LEVEL": "WARNING",
    }
    process = await asyncio.create_subprocess_exec(
        str(MENDWORK),
        "run",
        str(workflow),
        "--output",
        "json",
        "--artifacts-dir",
        str(artifacts),
        "--input",
        f"site_url={site}app.html",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    assert process.stdout is not None
    lines: list[dict[str, object]] = []
    try:
        async with asyncio.timeout(EVENT_TIMEOUT_S):
            while not lines or (lines[-1]["type"], lines[-1].get("step_id")) != (
                "action_performed",
                "export",
            ):
                line = await process.stdout.readline()
                assert line, f"the run ended before the export was sent: {lines}"
                lines.append(json.loads(line))
        started = await descendants(process.pid)
        for _ in range(signals):
            os.killpg(process.pid, signal.SIGINT)
        async with asyncio.timeout(EXIT_TIMEOUT_S):
            rest, errors = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    lines.extend(json.loads(line) for line in rest.splitlines())
    result = lines.pop() if lines and "result_version" in lines[-1] else None
    (run_directory,) = (artifacts / "runs").iterdir()
    assert process.returncode is not None
    return Interrupted(
        code=process.returncode,
        events=lines,
        result=result,
        stderr=errors.decode(),
        run=Run.model_validate_json((run_directory / "run.json").read_bytes()),
        outlived=await asyncio.to_thread(still_running, started, EXIT_TIMEOUT_S),
    )


async def descendants(root: int) -> set[int]:
    """Every process descended from ``root`` now: the Playwright driver, Chromium, its helpers."""
    listing = await asyncio.create_subprocess_exec(
        "ps", "-A", "-o", "pid=,ppid=", stdout=asyncio.subprocess.PIPE
    )
    output, _ = await listing.communicate()
    children: dict[int, set[int]] = {}
    for row in output.decode().splitlines():
        pid, parent = (int(field) for field in row.split())
        children.setdefault(parent, set()).add(pid)
    found: set[int] = set()
    frontier = [root]
    while frontier:
        for child in children.get(frontier.pop(), set()):
            if child not in found:
                found.add(child)
                frontier.append(child)
    return found


if sys.platform == "linux":

    def still_running(pids: set[int], timeout_s: float) -> set[int]:
        """The processes that have not exited within the timeout, waited on as exit events."""
        handles: dict[int, int] = {}
        for pid in pids:
            try:
                handles[os.pidfd_open(pid)] = pid
            except ProcessLookupError:
                continue
        poller = select.poll()
        for handle in handles:
            poller.register(handle, select.POLLIN)
        deadline = time.monotonic() + timeout_s
        try:
            waiting = set(handles)
            while waiting and (remaining := deadline - time.monotonic()) > 0:
                for handle, _ in poller.poll(remaining * 1000):
                    poller.unregister(handle)
                    waiting.discard(handle)
            return {handles[handle] for handle in waiting}
        finally:
            for handle in handles:
                os.close(handle)

else:

    def still_running(pids: set[int], timeout_s: float) -> set[int]:
        """The processes that have not exited within the timeout, waited on as exit events."""
        queue = select.kqueue()
        try:
            waiting: set[int] = set()
            for pid in pids:
                event = select.kevent(
                    pid,
                    filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT,
                )
                try:
                    queue.control([event], 0, 0)
                except ProcessLookupError:
                    continue
                waiting.add(pid)
            deadline = time.monotonic() + timeout_s
            while waiting and (remaining := deadline - time.monotonic()) > 0:
                for fired in queue.control(None, len(waiting), remaining):
                    waiting.discard(fired.ident)
            return waiting
        finally:
            queue.close()


@pytest.mark.asyncio
async def test_an_interrupt_cancels_a_run_that_dispatched_nothing_irreversible(
    fixture_site: str, tmp_path: Path
) -> None:
    outcome = await interrupt(fixture_site, tmp_path, risk="safe", signals=1)

    assert outcome.code == 130, outcome.stderr
    assert outcome.run.status is RunStatus.CANCELLED
    assert [step.status for step in outcome.run.steps] == [
        StepStatus.SUCCEEDED,
        StepStatus.CANCELLED,
    ]
    assert outcome.run.irreversible_dispatched == ()
    assert [event["sequence"] for event in outcome.events] == list(
        range(1, len(outcome.events) + 1)
    )
    assert outcome.events[-1]["type"] == "run_finished"
    assert outcome.result is not None
    assert outcome.result["exit_code"] == 130
    assert outcome.outlived == set()


@pytest.mark.asyncio
async def test_an_interrupt_after_an_irreversible_dispatch_leaves_the_run_needing_review(
    fixture_site: str, tmp_path: Path
) -> None:
    outcome = await interrupt(fixture_site, tmp_path, risk="irreversible", signals=1)

    assert outcome.code == 4, outcome.stderr
    assert outcome.run.status is RunStatus.NEEDS_REVIEW
    export = outcome.run.steps[1]
    assert export.status is StepStatus.CANCELLED
    assert export.error is not None
    assert export.error.context["irreversible_steps"] == ["export"]
    assert [item.step_id for item in outcome.run.irreversible_dispatched] == ["export"]
    assert outcome.events[-1]["type"] == "run_finished"
    assert outcome.outlived == set()


@pytest.mark.asyncio
async def test_a_second_interrupt_aborts_at_once_and_the_record_stays_consistent(
    fixture_site: str, tmp_path: Path
) -> None:
    outcome = await interrupt(fixture_site, tmp_path, risk="irreversible", signals=2)

    assert outcome.code == 4, outcome.stderr
    assert outcome.run.status is RunStatus.NEEDS_REVIEW
    assert [step.status for step in outcome.run.steps] == [
        StepStatus.SUCCEEDED,
        StepStatus.CANCELLED,
    ]
    assert [item.step_id for item in outcome.run.irreversible_dispatched] == ["export"]
    assert outcome.run.finished_at is not None
    assert outcome.outlived == set()
