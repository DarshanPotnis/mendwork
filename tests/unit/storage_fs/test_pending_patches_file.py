"""Pending patches in files: whole documents, private files, and one hold at a time."""

import asyncio
import stat
from pathlib import Path

import pytest

from mendwork.adapters.storage_fs.pending_patches import PENDING_DIRECTORY, FilePendingPatches
from mendwork.adapters.storage_fs.workflow_store import FileWorkflowStore
from mendwork.engine.domain.changes import Promotion
from mendwork.engine.domain.enums import PromotionPolicy
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.patches import PendingPatch
from mendwork.engine.domain.run_identifiers import parse_run_id
from mendwork.engine.errors import WorkflowStoreUnavailable
from mendwork.engine.patching.promotion import started
from tests.heal_changes import heal_change
from tests.unit.storage_fs.conftest import CODEC
from tests.workflows import CREATED_AT_DATETIME, version

pytestmark = pytest.mark.asyncio

DEMO = WorkflowId("demo")


def a_patch() -> PendingPatch:
    workflow = version()
    change = heal_change(workflow).model_copy(
        update={
            "promotion": Promotion(
                policy=PromotionPolicy.AFTER_N_SUCCESSES,
                runs=(parse_run_id("20260915T100000Z-00000001"),),
            )
        }
    )
    run_id = parse_run_id("20260915T100000Z-00000001")
    return started(DEMO, workflow.steps[2], change, run_id, CREATED_AT_DATETIME)


async def test_patches_survive_a_new_store_on_the_same_root_in_private_files(
    tmp_path: Path,
) -> None:
    patch = a_patch()
    async with FilePendingPatches(tmp_path).hold(DEMO) as hold:
        before = list(hold.patches)
        await hold.replace((patch,))
        after = list(hold.patches)

    assert (before, after) == ([], [patch])

    store = FilePendingPatches(tmp_path)
    assert await store.read(DEMO) == (patch,)
    assert stat.S_IMODE(store.document(DEMO).stat().st_mode) == 0o600
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert sorted(path.name for path in store.directory.iterdir()) == ["demo.json", "demo.lock"]


async def test_a_workflow_without_pending_patches_has_none(tmp_path: Path) -> None:
    assert await FilePendingPatches(tmp_path).read(DEMO) == ()


async def test_the_pending_directory_leaves_the_workflow_store_readable(tmp_path: Path) -> None:
    await FileWorkflowStore(tmp_path, CODEC).publish(version())
    async with FilePendingPatches(tmp_path).hold(DEMO) as hold:
        await hold.replace((a_patch(),))

    assert (tmp_path / PENDING_DIRECTORY).is_dir()
    assert await FileWorkflowStore(tmp_path, CODEC).versions(DEMO) == (1,)


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"{not json", "is not a document this store wrote"),
        (b'{"pending_version": 1, "workflow_id": "other", "patches": []}', "holds the pending"),
    ],
)
async def test_a_document_this_store_did_not_write_is_refused(
    tmp_path: Path, content: bytes, problem: str
) -> None:
    store = FilePendingPatches(tmp_path)
    store.directory.mkdir(parents=True)
    store.document(DEMO).write_bytes(content)

    with pytest.raises(WorkflowStoreUnavailable, match=problem):
        await store.read(DEMO)


async def test_a_symbolic_link_is_refused(tmp_path: Path) -> None:
    store = FilePendingPatches(tmp_path)
    store.directory.mkdir(parents=True)
    (tmp_path / "elsewhere.json").write_text("{}", encoding="utf-8")
    store.document(DEMO).symlink_to(tmp_path / "elsewhere.json")

    with pytest.raises(WorkflowStoreUnavailable, match="is a symbolic link"):
        await store.read(DEMO)


async def test_a_directory_that_cannot_be_created_makes_the_store_unavailable(
    tmp_path: Path,
) -> None:
    (tmp_path / PENDING_DIRECTORY).write_text(
        "a file where the directory belongs", encoding="utf-8"
    )
    store = FilePendingPatches(tmp_path)

    with pytest.raises(WorkflowStoreUnavailable):
        async with store.hold(DEMO):
            pytest.fail("the hold was taken without a directory")


async def test_a_second_hold_waits_until_the_first_one_ends(tmp_path: Path) -> None:
    order: list[str] = []
    first_held = asyncio.Event()
    release = asyncio.Event()

    async def first() -> None:
        async with FilePendingPatches(tmp_path).hold(DEMO) as hold:
            order.append("first held")
            first_held.set()
            await release.wait()
            await hold.replace((a_patch(),))
            order.append("first done")

    async def second() -> None:
        await first_held.wait()
        async with FilePendingPatches(tmp_path).hold(DEMO) as hold:
            order.append(f"second held with {len(hold.patches)}")

    waiting = asyncio.ensure_future(second())
    holding = asyncio.ensure_future(first())
    await first_held.wait()
    await asyncio.sleep(0)
    assert order == ["first held"]
    release.set()
    await asyncio.wait_for(asyncio.gather(holding, waiting), timeout=10)

    assert order == ["first held", "first done", "second held with 1"]
