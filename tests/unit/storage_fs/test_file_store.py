"""File-system specifics: layout, atomic no-overwrite publish, and integrity checks."""

import asyncio
import errno
import os
import re
import threading
from pathlib import Path

import pytest

from mendwork.adapters.storage_fs.file_ops import OsFileOps
from mendwork.adapters.storage_fs.workflow_store import FileWorkflowStore, version_file_name
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.errors import PolicyViolation, VersionConflict, WorkflowValidationError
from tests.unit.storage_fs.conftest import CODEC
from tests.unit.storage_fs.versions import child_of, lineage

pytestmark = pytest.mark.asyncio

DEMO = WorkflowId("demo")
WAIT_SECONDS = 5


def entries(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


async def test_one_directory_per_workflow_and_one_canonical_file_per_version(
    tmp_path: Path,
) -> None:
    store = FileWorkflowStore(tmp_path, CODEC)
    first, second = lineage(2)

    await store.publish(first)
    await store.publish(second)

    assert entries(tmp_path) == ["demo"]
    assert entries(tmp_path / "demo") == ["v0001.yaml", "v0002.yaml"]
    assert (tmp_path / "demo" / "v0002.yaml").read_bytes() == CODEC.encode(second)


async def test_version_file_names_are_zero_padded_and_grow_past_four_digits() -> None:
    assert [version_file_name(n) for n in (1, 42, 9999, 10000)] == [
        "v0001.yaml",
        "v0042.yaml",
        "v9999.yaml",
        "v10000.yaml",
    ]


class FailingFileOps(OsFileOps):
    """Real file operations, except one that fails the way a full or broken disk does."""

    def __init__(self, failing: str) -> None:
        self.failing = failing

    def write_all(self, descriptor: int, data: bytes) -> None:
        if self.failing == "write_all":
            super().write_all(descriptor, data[: len(data) // 2])
            raise OSError(errno.ENOSPC, "No space left on device")
        super().write_all(descriptor, data)

    def sync_file(self, descriptor: int) -> None:
        if self.failing == "sync_file":
            raise OSError(errno.EIO, "Input/output error")
        super().sync_file(descriptor)

    def link_exclusive(self, source: Path, destination: Path) -> None:
        if self.failing == "link_exclusive":
            raise OSError(errno.EPERM, "Operation not permitted")
        super().link_exclusive(source, destination)

    def sync_directory(self, directory: Path) -> None:
        if self.failing == "sync_directory" and directory.name == "demo":
            raise OSError(errno.EIO, "Input/output error")
        super().sync_directory(directory)


@pytest.mark.parametrize("failing", ["write_all", "sync_file", "link_exclusive"])
async def test_a_failure_mid_publish_leaves_no_partial_or_temporary_file(
    tmp_path: Path, failing: str
) -> None:
    first, second = lineage(2)
    await FileWorkflowStore(tmp_path, CODEC).publish(first)
    store = FileWorkflowStore(tmp_path, CODEC, file_ops=FailingFileOps(failing))

    with pytest.raises(OSError, match=r"No space left|Input/output|not permitted"):
        await store.publish(second)

    assert entries(tmp_path / "demo") == ["v0001.yaml"]
    assert await store.versions(DEMO) == (1,)


async def test_a_directory_sync_failure_is_reported_and_a_retry_is_safe(tmp_path: Path) -> None:
    first, second = lineage(2)
    await FileWorkflowStore(tmp_path, CODEC).publish(first)

    with pytest.raises(OSError, match="Input/output"):
        await FileWorkflowStore(tmp_path, CODEC, file_ops=FailingFileOps("sync_directory")).publish(
            second
        )
    await FileWorkflowStore(tmp_path, CODEC).publish(second)

    assert entries(tmp_path / "demo") == ["v0001.yaml", "v0002.yaml"]


class GatedFileOps(OsFileOps):
    """Pauses a publish after its temporary file is durable, and optionally before linking."""

    def __init__(
        self,
        barrier: threading.Barrier,
        written: threading.Event,
        link_after: threading.Event | None,
        linked: threading.Event,
    ) -> None:
        self.barrier = barrier
        self.written = written
        self.link_after = link_after
        self.linked = linked

    def close(self, descriptor: int) -> None:
        super().close(descriptor)
        self.written.set()
        self.barrier.wait()

    def link_exclusive(self, source: Path, destination: Path) -> None:
        if self.link_after is not None and not self.link_after.wait(WAIT_SECONDS):
            raise TimeoutError("the other writer never linked")
        try:
            super().link_exclusive(source, destination)
        finally:
            self.linked.set()


@pytest.mark.parametrize("winner", ["first", "second"])
async def test_concurrent_publishes_of_one_version_have_exactly_one_winner(
    tmp_path: Path, winner: str
) -> None:
    [base] = lineage(1)
    await FileWorkflowStore(tmp_path, CODEC).publish(base)
    contenders = [child_of(base, "Edit from writer one"), child_of(base, "Edit from writer two")]
    # Both writers pass every pre-check and make their temporary file durable before either
    # links; the test is the third party that releases them, and one links strictly first.
    barrier = threading.Barrier(3, timeout=WAIT_SECONDS)
    written = [threading.Event(), threading.Event()]
    linked = [threading.Event(), threading.Event()]
    first_index = 0 if winner == "first" else 1
    second_index = 1 - first_index
    ops = [
        GatedFileOps(
            barrier, written[i], None if i == first_index else linked[first_index], linked[i]
        )
        for i in range(2)
    ]
    stores = [FileWorkflowStore(tmp_path, CODEC, file_ops=ops[i]) for i in range(2)]

    racing = asyncio.gather(
        *(stores[i].publish(contenders[i]) for i in range(2)), return_exceptions=True
    )
    for event in written:
        assert await asyncio.to_thread(event.wait, WAIT_SECONDS)
    in_flight = entries(tmp_path / "demo")
    listed = await stores[0].versions(DEMO)
    await asyncio.to_thread(barrier.wait)
    results = await racing

    assert listed == (1,)
    assert len(in_flight) == 3
    assert [name for name in in_flight if name.startswith(".v0002.yaml.")] == in_flight[:2]
    assert results[first_index] is None
    assert isinstance(results[second_index], VersionConflict)
    assert (tmp_path / "demo" / "v0002.yaml").read_bytes() == CODEC.encode(contenders[first_index])
    assert entries(tmp_path / "demo") == ["v0001.yaml", "v0002.yaml"]


async def test_a_symlinked_workflow_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "store"
    root.mkdir()
    (root / "demo").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PolicyViolation, match="escapes the store root"):
        await FileWorkflowStore(root, CODEC).versions(DEMO)


async def test_a_symlinked_version_file_is_refused(tmp_path: Path) -> None:
    [first] = lineage(1)
    store = FileWorkflowStore(tmp_path, CODEC)
    await store.publish(first)
    (tmp_path / "demo" / "v0002.yaml").symlink_to(tmp_path / "demo" / "v0001.yaml")

    with pytest.raises(PolicyViolation, match="symbolic link"):
        await store.get(DEMO, 2)
    with pytest.raises(WorkflowValidationError, match=r"unexpected entry 'v0002\.yaml'"):
        await store.versions(DEMO)


@pytest.mark.parametrize("name", ["v2.yaml", "v00002.yaml", "notes.txt", "v0002.yml"])
async def test_unexpected_files_in_a_workflow_directory_are_an_integrity_error(
    tmp_path: Path, name: str
) -> None:
    [first] = lineage(1)
    store = FileWorkflowStore(tmp_path, CODEC)
    await store.publish(first)
    (tmp_path / "demo" / name).write_text("stray", encoding="utf-8")

    with pytest.raises(WorkflowValidationError, match=re.escape(f"unexpected entry '{name}'")):
        await store.latest(DEMO)


async def test_dotfiles_such_as_interrupted_temporary_files_are_ignored(tmp_path: Path) -> None:
    [first] = lineage(1)
    store = FileWorkflowStore(tmp_path, CODEC)
    await store.publish(first)
    (tmp_path / "demo" / ".v0002.yaml.abc123.tmp").write_bytes(b"half a file")
    (tmp_path / "demo" / ".DS_Store").write_bytes(b"")

    assert await store.versions(DEMO) == (1,)


async def test_a_gap_in_version_files_is_an_integrity_error(tmp_path: Path) -> None:
    first, second, third = lineage(3)
    store = FileWorkflowStore(tmp_path, CODEC)
    for published in (first, second, third):
        await store.publish(published)
    (tmp_path / "demo" / "v0002.yaml").unlink()

    with pytest.raises(WorkflowValidationError, match=r"not contiguous from 1: \[1, 3\]"):
        await store.versions(DEMO)


async def test_a_file_that_disagrees_with_its_path_is_refused(tmp_path: Path) -> None:
    [first] = lineage(1)
    store = FileWorkflowStore(tmp_path, CODEC)
    await store.publish(first)
    os.link(tmp_path / "demo" / "v0001.yaml", tmp_path / "demo" / "v0002.yaml")

    with pytest.raises(WorkflowValidationError, match="holds demo v1, not demo v2"):
        await store.get(DEMO, 2)


async def test_an_invalid_stored_file_names_its_path(tmp_path: Path) -> None:
    [first] = lineage(1)
    store = FileWorkflowStore(tmp_path, CODEC)
    await store.publish(first)
    path = tmp_path / "demo" / "v0001.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("risk: safe", "risk: unknown"), encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError) as caught:
        await store.get(DEMO, 1)

    assert caught.value.context["source"] == str(path)
    assert [issue.path for issue in caught.value.issues] == ["steps[0].risk"]


async def test_an_oversized_stored_file_is_refused_without_reading_it_all(tmp_path: Path) -> None:
    [first] = lineage(1)
    small = FileWorkflowStore(tmp_path, type(CODEC)(max_bytes=4096))
    await FileWorkflowStore(tmp_path, CODEC).publish(first)
    (tmp_path / "demo" / "v0001.yaml").write_bytes(b"# padding\n" * 1000)

    with pytest.raises(WorkflowValidationError, match="4096-byte limit"):
        await small.get(DEMO, 1)
