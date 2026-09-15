"""Local run records: one process holds a run at a time, and records read back or fail clearly."""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from mendwork.adapters.artifacts_local.records import LOCK_NAME, LocalRunRecords
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.engine.domain.runs import ArtifactName, Run, RunStatus, parse_run_id
from mendwork.engine.errors import RunBusy, UnknownRun
from mendwork.engine.replay.journal import encode_run

pytestmark = pytest.mark.asyncio

RUN_ID: Final = parse_run_id("20260914T100000Z-0000beef")
HOLD_LOCK: Final = (
    "import fcntl, sys\n"
    "handle = open(sys.argv[1], 'a+b')\n"
    "fcntl.flock(handle.fileno(), fcntl.LOCK_EX)\n"
    "print('held', flush=True)\n"
    "sys.stdin.read()\n"
)


def records(tmp_path: Path) -> tuple[LocalRunRecords, LocalArtifactStore]:
    store = LocalArtifactStore(tmp_path)
    return LocalRunRecords(store), store


async def test_a_claimed_run_cannot_be_claimed_again_until_it_is_released(tmp_path: Path) -> None:
    subject, _store = records(tmp_path)

    assert await subject.is_claimed(RUN_ID) is False
    async with subject.claim(RUN_ID):
        assert await subject.is_claimed(RUN_ID) is True
        with pytest.raises(RunBusy, match="another process"):
            async with subject.claim(RUN_ID):
                pytest.fail("a second claim must not succeed")
    assert await subject.is_claimed(RUN_ID) is False
    async with subject.claim(RUN_ID):
        pass


async def test_a_claim_held_by_another_process_ends_when_that_process_ends(tmp_path: Path) -> None:
    subject, store = records(tmp_path)
    lock = store.run_directory(RUN_ID) / LOCK_NAME
    lock.parent.mkdir(parents=True)
    holder = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        HOLD_LOCK,
        str(lock),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None
        assert (await holder.stdout.readline()).strip() == b"held"
        assert await subject.is_claimed(RUN_ID) is True
        with pytest.raises(RunBusy):
            async with subject.claim(RUN_ID):
                pytest.fail("a run another process holds must not be claimed")
    finally:
        holder.kill()
        await holder.wait()

    assert await subject.is_claimed(RUN_ID) is False
    async with subject.claim(RUN_ID):
        assert await subject.is_claimed(RUN_ID) is True


async def test_records_read_back_and_missing_or_broken_ones_are_unknown_runs(
    tmp_path: Path,
) -> None:
    subject, store = records(tmp_path)
    run = Run(
        run_id=RUN_ID,
        workflow_id="order_flow",
        workflow_version=1,
        status=RunStatus.SUCCEEDED,
        started_at=datetime(2026, 9, 14, tzinfo=UTC),
    )

    with pytest.raises(UnknownRun):
        await subject.load_run(RUN_ID)
    with pytest.raises(UnknownRun):
        await subject.load_workflow(RUN_ID)
    store.write_now(RUN_ID, ArtifactName("run.json"), encode_run(run))
    store.write_now(RUN_ID, ArtifactName("workflow.json"), b"{}")
    assert await subject.load_run(RUN_ID) == run
    assert await subject.load_workflow(RUN_ID) == b"{}"
    store.write_now(RUN_ID, ArtifactName("run.json"), b"{broken")
    with pytest.raises(UnknownRun, match="cannot be read"):
        await subject.load_run(RUN_ID)
