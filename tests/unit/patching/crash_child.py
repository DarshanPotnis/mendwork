"""Runs of the renamed ledger against real files, for the promotion crash test (ADR 0013).

``patched_run`` wires one run the way ``mendwork run`` does, except for the page: the version store,
pending patches, run records, and run artifacts are the real file adapters under one directory.
Run as a module, it performs one run whose process ends (``os._exit``) the instant the heal's
version is published, before the pending patches are replaced or the final run record is written:

    python -m tests.unit.patching.crash_child <directory> <immediate|after_n> <run number>
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Final

from mendwork.adapters.artifacts_local.records import LocalRunRecords
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.storage_fs.pending_patches import FilePendingPatches
from mendwork.adapters.storage_fs.workflow_store import FileWorkflowStore
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.runs import Run, RunId, parse_run_id
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.patcher import Patcher
from mendwork.engine.replay.replayer import Replayer
from tests.fakes.browser import FakeLauncher
from tests.fakes.clock import FakeClock
from tests.fakes.egress import TEST_POLICY, FakeResolver
from tests.fakes.ports import DictSecretResolver, RecordingEventSink, SequenceRandom
from tests.unit.patching.builders import (
    AFTER_TWO,
    IMMEDIATE,
    SAVES,
    WORKFLOW_ID,
    ledger_page,
    ledger_version,
)
from tests.unit.replay.builders import config
from tests.workflows import CREATED_AT_DATETIME

KILLED: Final = 137
"""The exit status of a process ended the way a SIGKILL would end it."""
CODEC: Final = WorkflowYamlCodec(max_bytes=1 << 20)
POLICIES: Final = {"immediate": IMMEDIATE, "after_n": AFTER_TWO}


def run_id_for(number: int) -> RunId:
    """A distinct run id for each run of the test, across processes."""
    return parse_run_id(f"20260915T180000Z-{number:08x}")


class OneRunId:
    """Hands out one given run id."""

    def __init__(self, run_id: RunId) -> None:
        self._run_id = run_id

    def new_run_id(self) -> RunId:
        return self._run_id


class EndsProcessAfterPublishing:
    """The real file store, whose process ends the moment a version is published."""

    def __init__(self, inner: FileWorkflowStore) -> None:
        self._inner = inner

    async def publish(self, version: WorkflowVersion) -> None:
        await self._inner.publish(version)
        os._exit(KILLED)

    async def get(self, workflow_id: WorkflowId, version: int) -> WorkflowVersion | None:
        return await self._inner.get(workflow_id, version)

    async def latest(self, workflow_id: WorkflowId) -> WorkflowVersion | None:
        return await self._inner.latest(workflow_id)

    async def versions(self, workflow_id: WorkflowId) -> tuple[int, ...]:
        return await self._inner.versions(workflow_id)


def stores(root: Path) -> tuple[FileWorkflowStore, FilePendingPatches, LocalArtifactStore]:
    """The workflow store, pending patches, and run artifacts under a directory."""
    return (
        FileWorkflowStore(root / "store", CODEC),
        FilePendingPatches(root / "store"),
        LocalArtifactStore(root / "artifacts"),
    )


async def patched_run(
    root: Path,
    workflow: WorkflowVersion,
    *,
    policy: str,
    number: int,
    end_after_publishing: bool = False,
) -> Run:
    """One run of the workflow on the renamed ledger page, saving heals into files under root."""
    store, pending, artifacts = stores(root)
    if not await store.versions(WORKFLOW_ID):
        await store.publish(ledger_version())
    page = ledger_page()
    patcher = Patcher(
        store=EndsProcessAfterPublishing(store) if end_after_publishing else store,
        pending=pending,
        clock=FakeClock(CREATED_AT_DATETIME),
        config=POLICIES[policy],
    )
    replayer = Replayer(
        launcher=FakeLauncher(page),
        artifacts=artifacts,
        records=LocalRunRecords(artifacts),
        events=RecordingEventSink(),
        secrets=DictSecretResolver({}),
        clock=FakeClock(CREATED_AT_DATETIME),
        timer=page.timer,
        randomness=SequenceRandom([0.0]),
        run_ids=OneRunId(run_id_for(number)),
        config=config(),
        egress=TEST_POLICY,
        resolver=FakeResolver(),
        patcher=patcher,
    )
    return await replayer.run(workflow, {}, source=SAVES)


def main(arguments: list[str]) -> int:
    root, policy, number = Path(arguments[0]), arguments[1], int(arguments[2])
    asyncio.run(
        patched_run(root, ledger_version(), policy=policy, number=number, end_after_publishing=True)
    )
    # Reaching this line means no version was published, which the test reports as a failure.
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
