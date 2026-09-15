"""Promotion inside a run: after the steps, before the final record, and never cut short."""

import asyncio

import pytest

from mendwork.engine.domain.patches import PatchResult
from mendwork.engine.domain.runs import RunStatus
from tests.unit.patching.builders import (
    WORKFLOW_ID,
    ScriptedStore,
    ledger,
    ledger_page,
    ledger_version,
)

pytestmark = pytest.mark.asyncio


async def test_the_version_is_published_before_the_final_record_that_mentions_it() -> None:
    made = await ledger()
    writes_at_publish: list[int] = []

    async def note_writes() -> None:
        writes_at_publish.append(len(made.artifacts.writes))

    store = ScriptedStore(made.store, before_publish=note_writes)
    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher(store=store))

    records = made.artifacts.record_writes(run.run_id)
    assert [item.result for item in records[-1].patches] == [PatchResult.PUBLISHED]
    assert all(record.patches == () for record in records[:-1])
    assert writes_at_publish == [len(made.artifacts.writes) - 1]


async def test_an_interrupt_during_promotion_waits_for_the_version_and_the_record() -> None:
    made = await ledger()
    publishing = asyncio.Event()
    release = asyncio.Event()

    async def slow_publish() -> None:
        publishing.set()
        await release.wait()

    store = ScriptedStore(made.store, before_publish=slow_publish)
    task = asyncio.ensure_future(
        made.run(ledger_page(), ledger_version(), patcher=made.patcher(store=store))
    )
    await publishing.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert await made.store.versions(WORKFLOW_ID) == (1, 2)
    [record] = [
        made.artifacts.run_record(run_id)
        for run_id, name in made.artifacts.files
        if name == "run.json"
    ]
    assert record.status is RunStatus.SUCCEEDED
    assert [(item.result, item.version) for item in record.patches] == [(PatchResult.PUBLISHED, 2)]
