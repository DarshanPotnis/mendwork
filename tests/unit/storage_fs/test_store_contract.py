"""The WorkflowStore contract, run against the in-memory fake and the file-system store."""

import pytest

from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.errors import VersionConflict, WorkflowValidationError
from mendwork.engine.ports.workflow_store import WorkflowStore
from tests.unit.storage_fs.versions import child_of, lineage

pytestmark = pytest.mark.asyncio

DEMO = WorkflowId("demo")


async def test_an_empty_store_has_no_versions(store: WorkflowStore) -> None:
    assert await store.versions(DEMO) == ()
    assert await store.latest(DEMO) is None
    assert await store.get(DEMO, 1) is None


async def test_published_versions_are_listed_and_the_latest_is_derived(
    store: WorkflowStore,
) -> None:
    first, second, third = lineage(3)
    for published in (first, second, third):
        await store.publish(published)

    assert await store.versions(DEMO) == (1, 2, 3)
    assert await store.latest(DEMO) == third
    assert await store.get(DEMO, 2) == second
    assert await store.get(DEMO, 4) is None
    assert await store.get(DEMO, 0) is None


async def test_a_taken_version_number_is_a_conflict(store: WorkflowStore) -> None:
    first, second = lineage(2)
    await store.publish(first)
    await store.publish(second)

    with pytest.raises(VersionConflict) as caught:
        await store.publish(child_of(first, "A competing edit"))

    assert dict(caught.value.context) == {"reason": "exists", "workflow_id": "demo", "version": 2}
    assert await store.get(DEMO, 2) == second


async def test_republishing_identical_content_is_a_no_op(store: WorkflowStore) -> None:
    first, second = lineage(2)
    await store.publish(first)
    await store.publish(second)

    await store.publish(second)

    assert await store.versions(DEMO) == (1, 2)


async def test_a_version_whose_parent_is_missing_is_a_conflict(store: WorkflowStore) -> None:
    first, _, third = lineage(3)
    await store.publish(first)

    with pytest.raises(VersionConflict) as caught:
        await store.publish(third)

    assert caught.value.context["reason"] == "parent_missing"
    assert await store.versions(DEMO) == (1,)


@pytest.mark.parametrize(
    "workflow_id", ["../escape", "a/b", "/etc", "..", "UPPER", "\u217eemo", "x" * 65, "nul\x00"]
)
async def test_ids_that_could_escape_the_root_are_refused(
    store: WorkflowStore, workflow_id: str
) -> None:
    with pytest.raises(WorkflowValidationError, match="lowercase slug"):
        await store.versions(WorkflowId(workflow_id))
    with pytest.raises(WorkflowValidationError, match="lowercase slug"):
        await store.get(WorkflowId(workflow_id), 1)


async def test_a_version_built_around_validation_is_still_refused(store: WorkflowStore) -> None:
    [first] = lineage(1)
    forged = first.model_copy(update={"workflow_id": "../../etc"})

    with pytest.raises(WorkflowValidationError, match="lowercase slug"):
        await store.publish(forged)
