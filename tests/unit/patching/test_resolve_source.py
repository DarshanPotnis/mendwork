"""A file's run resolved against the workflow store: first versions, races, an unusable store."""

from typing import Final

import pytest

from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.patches import NotSavedReason, WorkflowSource
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.sources import resolve_source, stored_history
from tests.fakes.workflow_store import InMemoryWorkflowStore
from tests.unit.patching.builders import ScriptedStore, with_intent
from tests.workflows import version

pytestmark = pytest.mark.asyncio

PATH: Final = "workflows/demo.yaml"


class ListsWhatItCannotFind(InMemoryWorkflowStore):
    """A store that lists version 1 but cannot read it back."""

    async def get(self, workflow_id: WorkflowId, version: int) -> WorkflowVersion | None:
        return None if version == 1 else await super().get(workflow_id, version)


async def test_exact_reads_nothing_from_the_store() -> None:
    store = ScriptedStore(InMemoryWorkflowStore(), unavailable=True)

    decision = await resolve_source(store, version(), PATH, exact=True)

    assert decision.source.not_saved is NotSavedReason.EXACT
    assert store.calls == 0


async def test_a_first_run_stores_the_file_as_version_1_and_a_rerun_finds_it() -> None:
    store = InMemoryWorkflowStore()
    file = version()

    first = await resolve_source(store, file, PATH, exact=False)
    again = await resolve_source(store, file, PATH, exact=False)

    assert (first.publish_first, first.version) == (True, file)
    assert await store.versions(file.workflow_id) == (1,)
    assert (again.publish_first, again.version, again.source) == (
        False,
        file,
        WorkflowSource(path=PATH, stored_version=1, saves_heals=True),
    )


async def test_a_first_version_another_process_stored_first_decides_the_run() -> None:
    inner = InMemoryWorkflowStore()
    file = version()
    other = file.model_copy(update={"steps": with_intent(file, "save", "Save it").steps})

    async def another_process() -> None:
        await inner.publish(other)

    store = ScriptedStore(inner, before_publish=another_process)
    decision = await resolve_source(store, file, PATH, exact=False)

    assert (decision.version, decision.publish_first) == (file, False)
    assert decision.source.not_saved is NotSavedReason.FILE_DIFFERS
    assert await inner.get(file.workflow_id, 1) == other


@pytest.mark.parametrize("problem", ["unavailable", "always_conflicts"])
async def test_a_store_that_cannot_be_used_leaves_the_file_to_run_as_written(problem: str) -> None:
    store = ScriptedStore(
        InMemoryWorkflowStore(),
        unavailable=problem == "unavailable",
        conflicts=problem == "always_conflicts",
    )
    file = version()

    decision = await resolve_source(store, file, PATH, exact=False)

    assert (decision.version, decision.publish_first) == (file, False)
    assert decision.source == WorkflowSource(
        path=PATH, saves_heals=False, not_saved=NotSavedReason.STORE_UNAVAILABLE
    )


async def test_history_leaves_out_a_listed_version_that_cannot_be_read() -> None:
    store = ListsWhatItCannotFind()
    file = version()
    await store.publish(file)

    assert await store.versions(file.workflow_id) == (1,)
    assert await stored_history(store, file.workflow_id) == ()
