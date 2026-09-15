"""Healing inside a run: gates, verification, recovery, limits, and the events a heal emits."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Final

import pytest
import structlog

from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import AbstentionReason, Verification
from mendwork.engine.domain.runs import Run, RunStatus, StepResult, StepStatus, parse_run_id
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import HealAbstained, NeedsReview, TargetNotFound
from mendwork.engine.healing.context import LadderContext
from mendwork.engine.healing.recovery import StateRestorer
from mendwork.engine.healing.run_state import RunHealState, StepStart
from mendwork.engine.ports.browser_types import NavigationOutcome, PlainText
from mendwork.engine.ports.element_types import Box
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.evidence import EvidenceRecorder
from mendwork.engine.replay.progress import StepProgress
from mendwork.engine.replay.replayer import Replayer
from mendwork.engine.replay.reports import TARGET_CONTEXT_KEY
from mendwork.engine.replay.step_actions import StepActions
from mendwork.engine.replay.step_healing import StepHealer
from mendwork.engine.replay.values import ValueResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser, FakeLauncher
from tests.fakes.clock import FakeClock
from tests.fakes.egress import TEST_POLICY, FakeResolver, navigation_guard
from tests.fakes.ports import (
    DictSecretResolver,
    InMemoryArtifactStore,
    InMemoryRunRecords,
    RecordingEventSink,
    SequenceRandom,
    SequentialRunIds,
)
from tests.unit.healing.builders import (
    EXPORT_TEST_ID,
    add_element,
    export_button,
    reference_field,
    step,
)
from tests.unit.replay.builders import browser, config, healing, selector
from tests.workflows import CREATED_AT_DATETIME, version

pytestmark = pytest.mark.asyncio

LEDGER_URL: Final = "https://ledger.example.test/ledger"
EXPORT: Final = export_button(selectors=[EXPORT_TEST_ID.model_dump()])
EXPORTED: Final = {"kind": "text_present", "text": "Ledger exported"}
FAR: Final = {"box": Box(x=0.0, y=0.9, width=0.1, height=0.05)}


def open_ledger() -> dict[str, object]:
    return {
        "id": "open",
        "intent": "Open the ledger",
        "action": "navigate",
        "risk": "safe",
        "value": {"kind": "literal", "value": LEDGER_URL},
    }


def click(
    step_id: str = "export",
    fingerprint: Fingerprint = EXPORT,
    *,
    risk: str = "safe",
    checkpoints: tuple[Mapping[str, object], ...] = (EXPORTED,),
) -> dict[str, object]:
    return {
        "id": step_id,
        "intent": f"Click {step_id}",
        "action": "click",
        "risk": risk,
        "target": fingerprint.model_dump(mode="json"),
        "checkpoints": list(checkpoints),
    }


def says(text: str) -> Callable[[FakeBrowser], None]:
    def effect(page: FakeBrowser) -> None:
        page.text = text

    return effect


async def replay(
    page: FakeBrowser, *steps: dict[str, object], **overrides: object
) -> tuple[Run, RecordingEventSink]:
    events = RecordingEventSink()
    replayer = Replayer(
        launcher=FakeLauncher(page),
        artifacts=InMemoryArtifactStore(),
        events=events,
        secrets=DictSecretResolver({}),
        clock=FakeClock(CREATED_AT_DATETIME),
        timer=page.timer,
        randomness=SequenceRandom([0.0]),
        run_ids=SequentialRunIds(),
        config=config(**overrides),
        egress=TEST_POLICY,
        resolver=FakeResolver(),
        records=InMemoryRunRecords(),
    )
    workflow: WorkflowVersion = version(steps=list(steps))
    return await replayer.run(workflow, {}), events


def result(run: Run, step_id: str) -> StepResult:
    return next(item for item in run.steps if item.step_id == step_id)


def step_events(events: RecordingEventSink, step_id: str) -> list[str]:
    return [event.type for event in events.events if getattr(event, "step_id", None) == step_id]


def two_exports(page: FakeBrowser, *, first_works: bool = False) -> None:
    """An unchanged-looking export button that does nothing, and a renamed one that works."""
    add_element(page, "a", EXPORT, on_action=says("Ledger exported") if first_works else None)
    add_element(
        page,
        "b",
        EXPORT,
        identity={"name": "Share ledger"},
        facts={"text": "Share ledger"},
        on_action=says("Ledger exported"),
    )


async def test_a_heal_is_attempted_verified_and_recorded_in_order() -> None:
    page = browser()
    add_element(
        page,
        "export",
        EXPORT,
        identity={"name": "Share ledger"},
        facts={"text": "Share ledger"},
        on_action=says("Ledger exported"),
    )
    page.finds[EXPORT_TEST_ID] = "export"

    run, events = await replay(page, open_ledger(), click())

    assert run.status is RunStatus.SUCCEEDED
    assert step_events(events, "export") == [
        "step_started", "heal_attempted", "heal_attempted", "heal_attempted", "target_resolved",
        "action_performed", "checkpoint_passed", "heal_verified", "step_succeeded",
    ]  # fmt: skip
    heal = result(run, "export").heal
    assert heal is not None
    assert (heal.healed_rung, heal.abstention) == (2, None)
    assert [(report.rung, report.verification) for report in heal.attempts] == [
        (0, Verification.NOT_PERFORMED),
        (1, Verification.NOT_PERFORMED),
        (2, Verification.PASSED),
    ]
    target = result(run, "export").target
    assert target is not None
    assert (target.healed_rung, target.identity.name if target.identity else None) == (
        2,
        "Share ledger",
    )
    verified = [event for event in events.events if event.type == "heal_verified"]
    assert [getattr(event, "passed", None) for event in verified] == [True]


async def test_a_heal_for_an_irreversible_step_stops_for_approval_without_acting() -> None:
    page = browser()
    add_element(page, "export", EXPORT)

    run, events = await replay(page, open_ledger(), click(risk="irreversible"))

    stopped = result(run, "export")
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert stopped.status is StepStatus.AWAITING_APPROVAL
    assert stopped.error is not None
    assert stopped.error.type == "ApprovalRequired"
    assert stopped.heal is not None
    assert stopped.heal.proposal is not None
    assert stopped.heal.proposal.candidate.identity.name == "Export ledger"
    assert not stopped.action_performed
    assert "click:export" not in page.calls
    assert page.key_of(page.released[-1]) == "export"
    failed = [event for event in events.events if event.type == "step_failed"]
    assert [getattr(event, "status", None) for event in failed] == [StepStatus.AWAITING_APPROVAL]


async def test_a_step_without_a_checkpoint_that_proves_its_effect_abstains() -> None:
    page = browser()
    add_element(page, "export", EXPORT, on_action=says("Ledger exported"))

    run, _ = await replay(page, open_ledger(), click(checkpoints=({"kind": "no_error_banner"},)))

    stopped = result(run, "export")
    assert stopped.error is not None
    assert (stopped.error.type, stopped.error.context["reason"]) == (
        "HealAbstained",
        "unverifiable",
    )
    assert "click:export" not in page.calls


async def test_a_failed_heal_restores_the_page_and_tries_the_next_candidate() -> None:
    page = browser()
    two_exports(page)

    run, events = await replay(page, open_ledger(), click())

    healed = result(run, "export")
    assert run.status is RunStatus.SUCCEEDED
    assert page.calls_named("click", "navigate") == [
        f"navigate:{LEDGER_URL}", "click:a", f"navigate:{LEDGER_URL}", "click:b",
    ]  # fmt: skip
    assert step_events(events, "export") == [
        "step_started", "heal_attempted", "heal_attempted", "heal_attempted", "target_resolved",
        "action_performed", "checkpoint_failed", "heal_verified", "state_restored",
        "heal_attempted", "heal_attempted", "heal_attempted", "target_resolved",
        "action_performed", "checkpoint_passed", "heal_verified", "step_succeeded",
    ]  # fmt: skip
    heal = healed.heal
    assert heal is not None
    assert [report.verification for report in heal.attempts if report.rung == 2] == [
        Verification.FAILED,
        Verification.PASSED,
    ]
    assert [report.attempt for report in heal.attempts if report.rung == 2] == [1, 2]
    [recovery] = heal.recoveries
    assert (recovery.restored, recovery.url, recovery.replayed, recovery.cleared_field) == (
        True, LEDGER_URL, (), False,
    )  # fmt: skip
    assert [checkpoint.passed for checkpoint in healed.checkpoints] == [True]


async def test_a_caution_fill_clears_the_wrong_field_before_restoring() -> None:
    field = reference_field(selectors=[selector(strategy="test_id", value="ref").model_dump()])
    page = browser()
    wrong = add_element(page, "a", field)
    right = add_element(
        page,
        "b",
        field,
        facts={"label_text": "Client code"},
        identity={"name": "Client code"},
        on_action=says("Reference saved"),
    )
    fill: dict[str, object] = {
        "id": "reference",
        "intent": "Fill the reference",
        "action": "fill",
        "risk": "caution",
        "target": field.model_dump(mode="json"),
        "value": {"kind": "literal", "value": "REF-1"},
        "checkpoints": [
            {"kind": "field_has_value"},
            {"kind": "text_present", "text": "Reference saved"},
        ],
    }

    run, _ = await replay(page, open_ledger(), fill)

    heal = result(run, "reference").heal
    assert run.status is RunStatus.SUCCEEDED
    assert heal is not None
    assert heal.recoveries[0].cleared_field
    assert wrong.value == ""
    assert right.value == "REF-1"
    assert PlainText(value="") in page.typed


async def test_an_authentication_step_gets_one_heal_attempt() -> None:
    sign_in = export_button(
        accessible_name="Log in", text="Log in", selectors=[EXPORT_TEST_ID.model_dump()]
    )
    page = browser()
    add_element(page, "a", sign_in)
    add_element(
        page,
        "b",
        sign_in,
        identity={"name": "Continue"},
        facts={"text": "Continue"},
        on_action=says("Ledger exported"),
    )

    run, events = await replay(page, open_ledger(), click("sign_in", sign_in, risk="caution"))

    stopped = result(run, "sign_in")
    assert stopped.error is not None
    assert stopped.error.context["reason"] == "authentication_limit"
    assert "authentication steps get 1 heal attempt" in stopped.error.message
    assert "state_restored" not in step_events(events, "sign_in")
    assert page.calls_named("click") == ["click:a"]


async def test_authentication_steps_can_be_kept_from_healing_at_all() -> None:
    sign_in = export_button(
        accessible_name="Log in", text="Log in", selectors=[EXPORT_TEST_ID.model_dump()]
    )
    page = browser()
    add_element(page, "a", sign_in, on_action=says("Ledger exported"))

    run, _ = await replay(
        page,
        open_ledger(),
        click("sign_in", sign_in, risk="caution"),
        healing=healing(authentication_max_attempts=0),
    )

    stopped = result(run, "sign_in")
    assert stopped.error is not None
    assert stopped.error.context["reason"] == "authentication_limit"
    assert page.calls_named("click") == []


async def test_a_step_stops_after_its_attempt_limit() -> None:
    page = browser()
    two_exports(page)

    run, _ = await replay(page, open_ledger(), click(), healing=healing(max_attempts=1))

    stopped = result(run, "export")
    assert stopped.error is not None
    assert (stopped.error.context["reason"], stopped.action_performed) == (
        "attempts_exhausted",
        True,
    )
    assert page.calls_named("click") == ["click:a"]


async def test_a_healed_target_that_cannot_take_the_action_is_skipped_without_acting() -> None:
    page = browser()
    add_element(page, "a", EXPORT, enabled=False)
    add_element(
        page,
        "b",
        EXPORT,
        identity={"name": "Share ledger"},
        facts={"text": "Share ledger"},
        on_action=says("Ledger exported"),
    )

    run, _ = await replay(page, open_ledger(), click())

    heal = result(run, "export").heal
    assert run.status is RunStatus.SUCCEEDED
    assert heal is not None
    assert [report.verification for report in heal.attempts if report.rung == 2] == [
        Verification.NOT_PERFORMED,
        Verification.PASSED,
    ]
    assert heal.recoveries == ()
    assert page.calls_named("click") == ["click:b"]


async def test_healing_stops_when_its_time_runs_out() -> None:
    page = browser()
    add_element(page, "a", EXPORT, enabled=False)

    run, _ = await replay(page, open_ledger(), click(), healing=healing(timeout_ms=500))

    stopped = result(run, "export")
    assert stopped.error is not None
    assert stopped.error.context["reason"] == "heal_timed_out"


async def test_a_restore_that_would_repeat_an_irreversible_step_is_refused() -> None:
    invite = export_button(
        accessible_name="Send invite",
        text="Send invite",
        attributes={"data_testid": "invite"},
        selectors=[selector(strategy="test_id", value="invite").model_dump()],
        structural_path="aside > button",
        nearby_text=["Team"],
    )
    page = browser()
    add_element(page, "invite", invite, candidate=False, on_action=says("Invite sent"))
    page.finds[selector(strategy="test_id", value="invite")] = "invite"
    two_exports(page)

    run, _ = await replay(
        page,
        open_ledger(),
        click(
            "invite",
            invite,
            risk="irreversible",
            checkpoints=({"kind": "text_present", "text": "Invite sent"},),
        ),
        click(),
    )

    stopped = result(run, "export")
    assert stopped.error is not None
    assert stopped.error.context["reason"] == "restore_failed"
    heal = stopped.heal
    assert heal is not None
    assert heal.recoveries[0].reason == "restoring would repeat step invite, which is irreversible"
    assert page.calls_named("click") == ["click:invite", "click:a"]


async def test_a_restore_that_lands_on_another_page_is_refused() -> None:
    page = browser()
    two_exports(page)
    page.navigations = [
        NavigationOutcome(url=LEDGER_URL, status=200),
        NavigationOutcome(url="https://ledger.example.test/signed-out", status=200),
    ]

    run, _ = await replay(page, open_ledger(), click())

    heal = result(run, "export").heal
    assert heal is not None
    assert heal.abstention is AbstentionReason.RESTORE_FAILED
    reason = heal.recoveries[0].reason
    assert reason is not None
    assert "the page opened at https://ledger.example.test/signed-out" in reason


async def test_when_the_recorded_selectors_work_again_after_a_restore_no_heal_is_needed() -> None:
    page = browser()
    add_element(page, "c", EXPORT, candidate=False, on_action=says("Ledger exported"))

    def reappear(fake: FakeBrowser) -> None:
        fake.finds[EXPORT_TEST_ID] = "c"

    add_element(page, "a", EXPORT, on_action=reappear)

    run, _ = await replay(page, open_ledger(), click())

    heal = result(run, "export").heal
    assert run.status is RunStatus.SUCCEEDED
    assert heal is not None
    assert (heal.healed_rung, len(heal.recoveries)) == (None, 1)
    assert page.calls_named("click") == ["click:a", "click:c"]


async def test_a_restore_replays_an_earlier_healed_step_with_its_verified_heal() -> None:
    show = export_button(
        accessible_name="Show ledger",
        text="Show ledger",
        attributes={"id": "show-ledger", "data_testid": "ledger-show", "type": "button"},
        selectors=[selector(strategy="test_id", value="ledger-show").model_dump()],
        structural_path="header > nav > button",
        nearby_text=["Navigation"],
        bbox={"x": 0.05, "y": 0.02, "width": 0.1, "height": 0.04},
    )
    page = browser()
    add_element(
        page,
        "shown",
        show,
        identity={"name": "Display ledger"},
        facts={"text": "Display ledger"},
        on_action=says("Ledger shown"),
    )
    two_exports(page)
    shown_check = {"kind": "text_present", "text": "Ledger shown"}

    run, events = await replay(
        page, open_ledger(), click("show", show, checkpoints=(shown_check,)), click()
    )

    assert run.status is RunStatus.SUCCEEDED
    assert page.calls_named("click") == ["click:shown", "click:a", "click:shown", "click:b"]
    assert step_events(events, "show").count("step_started") == 1
    heal = result(run, "export").heal
    assert heal is not None
    assert heal.recoveries[0].replayed == ("show",)


async def test_a_page_that_never_settles_is_not_healed() -> None:
    page = browser(quiet=False, unstable_reads=10_000)
    add_element(page, "export", EXPORT)

    run, _ = await replay(page, open_ledger(), click(), step_timeout_ms=500)

    stopped = result(run, "export")
    assert stopped.error is not None
    assert stopped.error.type == "PageNeverStable"
    assert stopped.heal is None


async def no_journal(index: int, target: Step) -> None:
    """These tests keep no run record, so an irreversible dispatch has nowhere to be journaled."""


def healer_for(
    page: FakeBrowser, target: Step, **kwargs: object
) -> tuple[StepHealer, RunHealState]:
    replay_config = config()
    scrubber = SecretScrubber()
    timer = page.timer
    log = structlog.stdlib.get_logger("tests.healing")
    run_id = parse_run_id("20260913T000000Z-00000001")
    emitter = RunEmitter(RecordingEventSink(), FakeClock(CREATED_AT_DATETIME), run_id)
    run_deadline = Deadline.after(timer, replay_config.run_timeout_ms)
    state = RunHealState()
    state.started(StepStart(0, target, "doc-0", page.url))
    guard = navigation_guard()
    actions = StepActions(
        browser=page,
        emitter=emitter,
        guard=guard,
        on_irreversible=no_journal,
        values=ValueResolver({}, DictSecretResolver({}), scrubber),
        evidence=EvidenceRecorder(
            browser=page,
            artifacts=InMemoryArtifactStore(),
            run_id=run_id,
            scrubber=scrubber,
            timeout_ms=replay_config.step_timeout_ms,
            log=log,
        ),
        scrubber=scrubber,
        timer=timer,
        randomness=SequenceRandom(),
        config=replay_config,
        run_deadline=run_deadline,
        log=log,
    )

    async def no_replay(start: StepStart, deadline: Deadline) -> None:
        raise AssertionError("nothing is replayed here")

    healer = StepHealer(
        browser=page,
        actions=actions,
        emitter=emitter,
        state=state,
        restorer=StateRestorer(
            browser=page,
            state=state,
            guard=guard,
            config=replay_config,
            timer=timer,
            randomness=SequenceRandom(),
            scrubber=scrubber,
            log=log,
            replay=no_replay,
        ),
        ladder=LadderContext(
            browser=page,
            config=replay_config.healing,
            settle_timeout_ms=100,
            quiet_frames=2,
            scrubber=scrubber,
        ),
        config=replay_config,
        timer=timer,
        run_deadline=run_deadline,
        scrubber=scrubber,
        log=log,
        **kwargs,  # type: ignore[arg-type] # only may_act is passed
    )
    return healer, state


def progress_for(target: Step) -> StepProgress:
    return StepProgress(
        index=0, step=target, started_at=datetime(2026, 9, 13, tzinfo=UTC), started=0.0
    )


def not_found() -> TargetNotFound:
    return TargetNotFound("nothing", reason="no_match", **{TARGET_CONTEXT_KEY: {"selectors": []}})


async def test_an_irreversible_action_on_a_heal_that_fails_verification_needs_review() -> None:
    page = browser()
    add_element(page, "a", EXPORT)
    target = step(click(risk=RiskLevel.IRREVERSIBLE.value))
    healer, _ = healer_for(page, target, may_act=lambda risk: True)
    progress = progress_for(target)

    with pytest.raises(NeedsReview):
        await healer.heal(progress, not_found())

    assert page.calls_named("click") == ["click:a"]
    assert progress.recoveries == []


async def test_a_replayed_step_without_a_verified_heal_keeps_its_rung0_failure() -> None:
    page = browser()
    target = step(click())
    healer, _ = healer_for(page, target)
    failure = not_found()

    with pytest.raises(TargetNotFound) as caught:
        await healer.reuse(progress_for(target), failure, Deadline.after(page.timer, 1_000))

    assert caught.value is failure


async def test_a_replayed_step_whose_verified_heal_is_gone_cannot_be_replayed() -> None:
    page = browser()
    add_element(
        page, "b", EXPORT, identity={"name": "Share ledger"}, facts={"text": "Share ledger"}
    )
    target = step(click())
    healer, state = healer_for(page, target)
    state.remember_verified(target.id, ("something", "else"), 2)

    with pytest.raises(HealAbstained) as caught:
        await healer.reuse(progress_for(target), not_found(), Deadline.after(page.timer, 1_000))

    assert caught.value.context["reason"] == "verified_heal_not_found"
    assert page.key_of(page.released[-1]) == "b"
