"""Stores under test: the in-memory fake and the file-system store share one contract."""

from collections.abc import Callable
from pathlib import Path

import pytest

from mendwork.adapters.storage_fs.workflow_store import FileWorkflowStore
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.ports.workflow_store import WorkflowStore
from tests.fakes.workflow_store import InMemoryWorkflowStore

CODEC = WorkflowYamlCodec(max_bytes=1 << 20)


@pytest.fixture
def file_store(tmp_path: Path) -> FileWorkflowStore:
    return FileWorkflowStore(tmp_path / "store", CODEC)


@pytest.fixture(params=["memory", "files"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> WorkflowStore:
    factories: dict[str, Callable[[], WorkflowStore]] = {
        "memory": InMemoryWorkflowStore,
        "files": lambda: FileWorkflowStore(tmp_path / "store", CODEC),
    }
    return factories[request.param]()
