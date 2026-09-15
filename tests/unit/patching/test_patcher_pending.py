"""The ``after_n_successes`` policy: pending patches, first tries, and when they become versions."""

from datetime import timedelta

import pytest

from mendwork.engine.domain.changes import HealChange, Promotion
from mendwork.engine.domain.enums import PromotionPolicy
from mendwork.engine.domain.lineage import edit_version, heal_version, roll_back_version
from mendwork.engine.domain.patches import PatchResult, SuccessKind, step_digest
from mendwork.engine.domain.run_identifiers import parse_run_id
from mendwork.engine.patching.config import PatchingConfig
from mendwork.engine.patching.promotion import counted, first_tries, started
from tests.fakes.pending_patches import InMemoryPendingPatches
from tests.heal_changes import heal_change, renamed_target, target_of
from tests.unit.patching.builders import (
    AFTER_TWO,
    IMMEDIATE,
    RECORDED_NAME,
    RENAMED,
    WORKFLOW_ID,
    Ledger,
    ScriptedStore,
    ledger,
    ledger_page,
    ledger_version,
    result,
    with_intent,
)

pytestmark = pytest.mark.asyncio


async def pending_after_one_run(made: Ledger) -> None:
    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher(AFTER_TWO))
    [outcome] = run.patches
    assert (outcome.result, outcome.successes, outcome.required) == (PatchResult.PENDING, 1, 2)


async def test_a_heal_waits_as_a_pending_patch_until_enough_runs_verified_it() -> None:
    made = await ledger()

    await pending_after_one_run(made)

    assert await made.store.versions(WORKFLOW_ID) == (1,)
    [patch] = made.pending.documents[WORKFLOW_ID]
    assert patch.base_step_sha256 == step_digest(ledger_version().steps[1])
    assert [success.how for success in patch.successes] == [SuccessKind.HEALED]
    assert patch.change.new_target.accessible_name == RENAMED


async def test_a_first_try_verifies_the_pending_target_and_the_second_success_publishes_it() -> (
    None
):
    made = await ledger()
    await pending_after_one_run(made)
    page = ledger_page()

    run = await made.run(page, ledger_version(), patcher=made.patcher(AFTER_TWO))

    step = result(run, "export")
    assert step.heal is None
    assert page.scans == []
    assert step.target is not None
    [patch_id] = {step.target.pending_patch}
    [outcome] = run.patches
    assert (outcome.result, outcome.version, outcome.successes, outcome.pending_id) == (
        PatchResult.PUBLISHED,
        2,
        2,
        patch_id,
    )
    second = await made.latest()
    assert isinstance(second.change, HealChange)
    assert second.change.promotion.policy is PromotionPolicy.AFTER_N_SUCCESSES
    assert len(second.change.promotion.runs) == 2
    assert made.pending.documents[WORKFLOW_ID] == ()


async def test_a_version_is_published_before_the_pending_patches_are_replaced() -> None:
    made = await ledger()
    await pending_after_one_run(made)
    log: list[str] = []
    store = ScriptedStore(made.store, log=log)
    made.pending.log = log

    await made.run(ledger_page(), ledger_version(), patcher=made.patcher(AFTER_TWO, store=store))

    assert [entry for entry in log if entry != "read"] == ["hold", "publish:v2", "replace"]


async def test_a_run_counts_once_however_often_it_verified_a_patch() -> None:
    made = await ledger()
    await pending_after_one_run(made)
    [patch] = made.pending.documents[WORKFLOW_ID]
    run_id = patch.successes[0].run_id

    again = counted(patch, run_id, made.clock.now(), SuccessKind.FIRST_TRY)

    assert again == patch


async def test_the_page_going_back_to_the_recorded_element_discards_the_pending_patch() -> None:
    made = await ledger()
    await pending_after_one_run(made)

    run = await made.run(
        ledger_page(RECORDED_NAME), ledger_version(), patcher=made.patcher(AFTER_TWO)
    )

    [outcome] = run.patches
    assert (outcome.result, outcome.detail) == (
        PatchResult.DISCARDED,
        "the recorded element was found again",
    )
    assert made.pending.documents[WORKFLOW_ID] == ()


async def test_an_edit_to_the_step_retires_its_pending_patch_and_a_new_heal_starts_over() -> None:
    made = await ledger()
    await pending_after_one_run(made)
    first = ledger_version()
    second = edit_version(
        first, with_intent(first, "export", "Export the ledger"), summary="e", clock=made.clock
    )
    await made.store.publish(second)
    page = ledger_page()

    run = await made.run(page, second, patcher=made.patcher(AFTER_TWO))

    assert page.scans != []
    assert [(item.result, item.detail) for item in run.patches] == [
        (PatchResult.DISCARDED, "the step changed since the heal was verified"),
        (PatchResult.PENDING, None),
    ]
    [patch] = made.pending.documents[WORKFLOW_ID]
    assert patch.base_step_sha256 == step_digest(second.steps[1])


async def test_a_pending_patch_left_behind_by_a_stopped_process_is_found_already_applied() -> None:
    made = await ledger()
    await pending_after_one_run(made)
    [patch] = made.pending.documents[WORKFLOW_ID]
    second = heal_version(ledger_version(), patch.change, clock=made.clock)
    await made.store.publish(second)

    run = await made.run(ledger_page(), second, patcher=made.patcher(AFTER_TWO))

    assert result(run, "export").heal is None
    assert [(item.result, item.detail) for item in run.patches] == [
        (PatchResult.DISCARDED, "v2 already targets this element")
    ]
    assert made.pending.documents[WORKFLOW_ID] == ()
    assert await made.store.versions(WORKFLOW_ID) == (1, 2)


@pytest.mark.parametrize("patching", [IMMEDIATE, AFTER_TWO], ids=["immediate", "after_n"])
async def test_a_heal_a_person_rolled_back_is_not_saved_again(patching: PatchingConfig) -> None:
    made = await ledger()
    first = ledger_version()
    await made.run(ledger_page(), first, patcher=made.patcher(IMMEDIATE))
    second = await made.latest()
    await made.store.publish(roll_back_version(second, first, reason="wrong", clock=made.clock))

    run = await made.run(ledger_page(), await made.latest(), patcher=made.patcher(patching))

    [outcome] = run.patches
    assert (outcome.result, outcome.version) == (PatchResult.PREVIOUSLY_ROLLED_BACK, 3)
    assert made.pending.documents.get(WORKFLOW_ID, ()) == ()
    assert await made.store.versions(WORKFLOW_ID) == (1, 2, 3)


async def test_pending_targets_are_tried_most_verified_first_then_oldest() -> None:
    made = await ledger()
    workflow = ledger_version()
    step = workflow.steps[1]
    at = made.clock.now()
    first_run = parse_run_id("20260915T100000Z-00000001")
    second_run = parse_run_id("20260915T100000Z-00000002")
    older = started(workflow.workflow_id, step, _change(workflow, "Share"), first_run, at)
    newer = started(
        workflow.workflow_id, step, _change(workflow, "Send"), first_run, at + timedelta(minutes=1)
    )
    newer = counted(newer, second_run, at, SuccessKind.FIRST_TRY)
    unrelated = started(workflow.workflow_id, step, _change(workflow, "Post"), first_run, at)
    unrelated = unrelated.model_copy(update={"base_step_sha256": "0" * 64})

    tries = first_tries(workflow, (older, unrelated, newer))

    assert [item.patch_id for item in tries[step.id]] == [newer.id, older.id]


async def test_no_pending_patch_is_read_under_the_immediate_policy() -> None:
    made = await ledger()
    unreadable = InMemoryPendingPatches(fail_reads=True)

    assert await made.patcher(IMMEDIATE, pending=unreadable).first_tries(ledger_version()) == {}
    assert unreadable.log == []


async def test_unreadable_pending_patches_are_not_tried_and_not_counted() -> None:
    made = await ledger()
    unreadable = InMemoryPendingPatches(fail_reads=True)
    patcher = made.patcher(AFTER_TWO, pending=unreadable)

    assert await patcher.first_tries(ledger_version()) == {}
    run = await made.run(ledger_page(), ledger_version(), patcher=patcher)

    assert [item.result for item in run.patches] == [PatchResult.STORE_UNAVAILABLE]
    assert await made.store.versions(WORKFLOW_ID) == (1,)


def _change(workflow: object, name: str) -> HealChange:
    assert hasattr(workflow, "steps")
    version = ledger_version()
    new = renamed_target(target_of(version, "export"), name)
    change = heal_change(version, "export", new_target=new)
    return change.model_copy(
        update={
            "promotion": Promotion(
                policy=PromotionPolicy.AFTER_N_SUCCESSES, runs=change.promotion.runs
            )
        }
    )
