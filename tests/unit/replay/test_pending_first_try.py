"""Pending patches' first tries: tried once at Rung 0 after the recorded target, before a ladder."""

import pytest

from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.patches import PendingPatch
from mendwork.engine.domain.runs import RunStatus
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.patching.promotion import started
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.rung0 import resolve_target
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button
from tests.unit.patching.builders import (
    AFTER_TWO,
    EXPORT,
    WORKFLOW_ID,
    Ledger,
    ledger,
    ledger_page,
    ledger_steps,
    ledger_version,
    result,
    says,
)
from tests.unit.replay.builders import selector

pytestmark = pytest.mark.asyncio


async def seeded(made: Ledger) -> PendingPatch:
    """One pending patch for the export step, from a first run on the renamed page."""
    await made.run(ledger_page(), ledger_version(), patcher=made.patcher(AFTER_TWO))
    [patch] = made.pending.documents[WORKFLOW_ID]
    return patch


def pointing_at(patch: PendingPatch, target: Fingerprint) -> PendingPatch:
    """The same pending patch, with another target."""
    change = patch.change.model_copy(update={"new_target": target})
    return patch.model_copy(update={"change": change})


def decoy(page: FakeBrowser, *, enabled: bool) -> Fingerprint:
    """A second button a pending patch could point at, found by its own test id."""
    test_id: Selector = selector(strategy="test_id", value="ledger-decoy")
    fingerprint = EXPORT.model_copy(
        update={"accessible_name": "Print ledger", "selectors": (test_id,)}
    )
    add_element(page, "decoy", fingerprint, candidate=False, enabled=enabled)
    page.finds[test_id] = "decoy"
    return fingerprint


async def test_a_drifted_recorded_target_tries_the_pending_target_before_any_scan() -> None:
    made = await ledger()
    patch = await seeded(made)
    page = ledger_page()

    run = await made.run(page, ledger_version(), patcher=made.patcher(AFTER_TWO))

    step = result(run, "export")
    assert run.status is RunStatus.SUCCEEDED
    assert (step.heal, page.scans) == (None, [])
    assert step.target is not None
    assert (step.target.pending_patch, step.target.healed_rung) == (patch.id, None)
    assert "click:export" in page.calls


async def test_an_ambiguous_recorded_target_goes_to_the_ladder_without_a_first_try() -> None:
    made = await ledger()
    await seeded(made)
    page = ledger_page()
    page.finds[EXPORT_TEST_ID] = (2,)

    run = await made.run(page, ledger_version(), patcher=made.patcher(AFTER_TWO))

    step = result(run, "export")
    assert page.scans != []
    assert step.target is None or step.target.pending_patch is None


async def test_a_pending_target_that_is_not_on_the_page_leaves_the_step_to_the_ladder() -> None:
    made = await ledger()
    patch = await seeded(made)
    nowhere = patch.change.new_target.model_copy(update={"accessible_name": "Nowhere"})
    made.pending.documents[WORKFLOW_ID] = (pointing_at(patch, nowhere),)
    page = ledger_page()

    run = await made.run(page, ledger_version(), patcher=made.patcher(AFTER_TWO))

    step = result(run, "export")
    assert step.heal is not None
    assert step.heal.healed_rung == 2


async def test_a_pending_target_that_cannot_take_the_action_is_left_for_the_ladder() -> None:
    made = await ledger()
    patch = await seeded(made)
    page = ledger_page()
    made.pending.documents[WORKFLOW_ID] = (pointing_at(patch, decoy(page, enabled=False)),)

    run = await made.run(page, ledger_version(), patcher=made.patcher(AFTER_TWO))

    step = result(run, "export")
    assert run.status is RunStatus.SUCCEEDED
    assert step.heal is not None
    assert "click:decoy" not in page.calls


@pytest.mark.parametrize(
    ("risk", "status", "error"),
    [
        (RiskLevel.SAFE, RunStatus.FAILED, "CheckpointFailed"),
        (RiskLevel.IRREVERSIBLE, RunStatus.NEEDS_REVIEW, "NeedsReview"),
    ],
)
async def test_a_pending_target_whose_checkpoints_fail_stops_the_step(
    risk: RiskLevel, status: RunStatus, error: str
) -> None:
    made = await ledger()
    patch = await seeded(made)
    workflow = ledger_version(steps=ledger_steps(risk=risk.value))
    made.pending.documents[WORKFLOW_ID] = (
        started(
            WORKFLOW_ID,
            workflow.steps[1],
            patch.change,
            patch.successes[0].run_id,
            patch.created_at,
        ),
    )
    page = ledger_page(exports=False)

    run = await made.run(page, workflow, patcher=made.patcher(AFTER_TWO))

    assert run.status is status
    step = result(run, "export")
    assert step.error is not None
    assert step.error.type == error
    assert step.heal is None
    assert step.target is not None
    assert step.target.pending_patch is not None
    assert run.patches == ()


async def test_a_restore_replays_an_earlier_step_through_its_pending_target() -> None:
    made = await ledger()
    await seeded(made)
    printed = export_button(
        accessible_name="Print ledger",
        text="Print ledger",
        attributes={"id": "print-ledger", "data_testid": "ledger-print", "type": "button"},
        selectors=[selector(strategy="test_id", value="ledger-print").model_dump()],
    )
    steps = [
        *ledger_steps(),
        {
            "id": "print",
            "intent": "Click the 'Print ledger' button",
            "action": "click",
            "risk": "safe",
            "target": printed.model_dump(mode="json"),
            "checkpoints": [{"kind": "text_present", "text": "Ledger printed"}],
        },
    ]
    page = ledger_page()
    add_element(page, "print_a", printed)
    add_element(
        page,
        "print_b",
        printed,
        identity={"name": "Print report"},
        facts={"text": "Print report"},
        on_action=says("Ledger printed"),
    )

    run = await made.run(page, ledger_version(steps=steps), patcher=made.patcher(AFTER_TWO))

    assert run.status is RunStatus.SUCCEEDED
    assert page.calls_named("click") == [
        "click:export", "click:print_a", "click:export", "click:print_b",
    ]  # fmt: skip
    heal = result(run, "print").heal
    assert heal is not None
    [recovery] = heal.recoveries
    assert (recovery.restored, recovery.replayed) == (True, ("export",))


async def test_an_impatient_resolution_does_not_wait_for_the_page_to_change() -> None:
    page = ledger_page()
    del page.finds[EXPORT_TEST_ID]

    with pytest.raises(TargetNotFound):
        await resolve_target(
            page,
            EXPORT,
            deadline=Deadline.after(page.timer, 5_000),
            settle_timeout_ms=100,
            quiet_frames=2,
            scrubber=SecretScrubber(),
            patient=False,
        )

    assert "wait_for_dom_change" not in page.calls
