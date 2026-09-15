"""Writes to one artifact land in the order they were asked for, whichever thread finishes first."""

import asyncio
import threading
from pathlib import Path
from typing import Final

import pytest

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.engine.domain.runs import ArtifactName, parse_run_id

pytestmark = pytest.mark.asyncio

RUN_ID: Final = parse_run_id("20260914T100000Z-0000f00d")
RECORD: Final = ArtifactName("run.json")


async def test_a_write_asked_for_earlier_never_replaces_a_later_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalArtifactStore(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = store._write_in_order

    def held_back(path: Path, data: bytes, order: int) -> None:
        # The earlier write's thread is held back until the later write has landed, which is
        # what a slow worker thread does when an abort writes the record synchronously.
        if data == b"running":
            entered.set()
            release.wait()
        original(path, data, order)

    monkeypatch.setattr(store, "_write_in_order", held_back)
    earlier = asyncio.ensure_future(store.write(RUN_ID, RECORD, b"running"))
    try:
        await asyncio.to_thread(entered.wait)
        store.write_now(RUN_ID, RECORD, b"cancelled")
    finally:
        release.set()
    await earlier

    assert store.read_now(RUN_ID, RECORD) == b"cancelled"


async def test_writes_in_sequence_leave_the_last_one(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)

    for content in (b"one", b"two", b"three"):
        await store.write(RUN_ID, RECORD, content)

    assert store.read_now(RUN_ID, RECORD) == b"three"
    assert (store.root, store.read_now(RUN_ID, ArtifactName("absent.json"))) == (tmp_path, None)
