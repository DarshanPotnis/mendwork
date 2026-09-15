"""The ``immediate`` policy: a succeeded run's verified heal becomes the next version (ADR 0013)."""

import pytest

from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.lineage import edit_version
from mendwork.engine.domain.patches import PatchResult
from mendwork.engine.domain.runs import RunStatus
from tests.fakes.pending_patches import InMemoryPendingPatches
from tests.heal_changes import target_of
from tests.unit.patching.builders import (
    DIFFERS,
    EXPORT,
    RENAMED,
    WORKFLOW_ID,
    ScriptedStore,
    crafted_run,
    ledger,
    ledger_page,
    ledger_version,
    result,
    with_found,
    with_intent,
)
from tests.workflows import fill_step, navigate_step, version

pytestmark = pytest.mark.asyncio


async def test_a_verified_heal_becomes_the_next_version_and_the_run_says_so() -> None:
    made = await ledger()

    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher())

    assert run.status is RunStatus.SUCCEEDED
    [outcome] = run.patches
    assert (outcome.step_id, outcome.result, outcome.version, outcome.rung, outcome.strength) == (
        "export",
        PatchResult.PUBLISHED,
        2,
        2,
        VerificationStrength.STRONG,
    )
    second = await made.latest()
    assert (second.version, second.parent_version) == (2, 1)
    assert isinstance(second.change, HealChange)
    assert second.change.evidence.run_id == run.run_id
    assert target_of(second, "export").accessible_name == RENAMED
    assert made.artifacts.run_record(run.run_id).patches == run.patches


async def test_a_rerun_on_the_same_page_needs_no_heal_and_publishes_nothing() -> None:
    made = await ledger()
    await made.run(ledger_page(), ledger_version(), patcher=made.patcher())
    page = ledger_page()

    rerun = await made.run(page, await made.latest(), patcher=made.patcher())

    assert rerun.status is RunStatus.SUCCEEDED
    assert result(rerun, "export").heal is None
    assert page.scans == []
    assert rerun.patches == ()
    assert await made.store.versions(WORKFLOW_ID) == (1, 2)


async def test_promoting_the_same_run_again_finds_the_heal_already_applied() -> None:
    made = await ledger()
    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher())

    again = await made.patcher().promote(run, ledger_version())

    assert [(item.result, item.version) for item in again] == [(PatchResult.ALREADY_APPLIED, 2)]
    assert await made.store.versions(WORKFLOW_ID) == (1, 2)


async def test_a_version_published_meanwhile_that_keeps_the_step_is_built_on() -> None:
    made = await ledger()
    first = ledger_version()
    edit = edit_version(
        first,
        with_intent(first, "open", "Open this quarter's ledger"),
        summary="e",
        clock=made.clock,
    )

    async def another_process_publishes() -> None:
        await made.store.publish(edit)

    store = ScriptedStore(made.store, before_publish=another_process_publishes)
    run = await made.run(ledger_page(), first, patcher=made.patcher(store=store))

    assert [(item.result, item.version) for item in run.patches] == [(PatchResult.PUBLISHED, 3)]
    third = await made.latest()
    assert third.steps[0].intent == "Open this quarter's ledger"
    assert target_of(third, "export").accessible_name == RENAMED
    assert store.log == ["publish:v3"]


async def test_a_heal_of_a_step_changed_meanwhile_is_stale() -> None:
    made = await ledger()
    first = ledger_version()
    changed = with_intent(first, "export", "Export the ledger")
    await made.store.publish(edit_version(first, changed, summary="e", clock=made.clock))

    run = await made.run(ledger_page(), first, patcher=made.patcher())

    [outcome] = run.patches
    assert (outcome.result, outcome.version, outcome.detail) == (
        PatchResult.STALE,
        2,
        "the step changed by v2",
    )
    assert await made.store.versions(WORKFLOW_ID) == (1, 2)


async def test_publishes_that_keep_conflicting_are_reported_once_the_attempts_run_out() -> None:
    made = await ledger()
    store = ScriptedStore(made.store, conflicts=True)

    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher(store=store))

    [outcome] = run.patches
    assert (outcome.result, outcome.detail) == (
        PatchResult.CONFLICT,
        "other versions were published first, 3 times",
    )
    assert await made.store.versions(WORKFLOW_ID) == (1,)


async def test_an_unavailable_store_leaves_the_run_succeeded_and_says_why() -> None:
    made = await ledger()
    store = ScriptedStore(made.store, unavailable=True)

    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher(store=store))

    assert run.status is RunStatus.SUCCEEDED
    [outcome] = run.patches
    assert outcome.result is PatchResult.STORE_UNAVAILABLE
    assert outcome.detail == (
        "WorkflowStoreUnavailable: the workflow store is unavailable on purpose"
    )


async def test_heals_that_are_not_saved_touch_no_store() -> None:
    made = await ledger()
    store = ScriptedStore(made.store)
    pending = InMemoryPendingPatches()

    run = await made.run(
        ledger_page(),
        ledger_version(),
        source=DIFFERS,
        patcher=made.patcher(store=store, pending=pending),
    )

    assert [item.result for item in run.patches] == [PatchResult.NOT_SAVED]
    assert (store.calls, pending.log) == (0, [])


async def test_a_run_without_a_source_records_no_patch_outcome() -> None:
    made = await ledger()

    run = await made.run(ledger_page(), ledger_version(), source=None, patcher=made.patcher())

    assert run.patches == ()
    assert await made.store.versions(WORKFLOW_ID) == (1,)


async def test_a_heal_captured_exactly_as_recorded_is_refused_as_no_change() -> None:
    made = await ledger()
    run = await made.run(ledger_page(), ledger_version())

    outcomes = await made.patcher().promote(with_found(run, "export", EXPORT), ledger_version())

    assert [item.result for item in outcomes] == [PatchResult.REFUSED]
    assert await made.store.versions(WORKFLOW_ID) == (1,)


async def test_a_new_target_the_step_cannot_use_is_refused() -> None:
    made = await ledger()
    workflow = version(
        workflow_id="form",
        steps=[navigate_step(), fill_step(checkpoints=[{"kind": "field_has_value"}])],
    )
    await made.store.publish(workflow)
    old = target_of(workflow, "fill_name")
    password = old.model_copy(
        update={"attributes": old.attributes.model_copy(update={"type": "password"})}
    )

    outcomes = await made.patcher().promote(crafted_run(workflow, "fill_name", password), workflow)

    [outcome] = outcomes
    assert (outcome.result, outcome.strength) == (PatchResult.REFUSED, VerificationStrength.WEAK)
    assert outcome.detail == "step fill_name cannot use the healed target"
