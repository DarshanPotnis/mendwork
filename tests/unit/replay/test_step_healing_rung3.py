"""Rung 3 inside a run: no call on the happy path, a model heal verified like any other, recovery
that asks again, approval for irreversible steps, and restores that never ask twice."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Final

import pytest

from mendwork.adapters.models.fake import FakeModel
from mendwork.engine.domain.events import HealVerifiedEvent, RunFinishedEvent
from mendwork.engine.domain.heals import Verification
from mendwork.engine.domain.model_evidence import ModelUsageTotals
from mendwork.engine.domain.runs import Run, RunStatus, StepResult, StepStatus
from mendwork.engine.healing.model_rung import ModelChoiceConfig, ModelRung
from mendwork.engine.ports.element_types import Box
from mendwork.engine.replay.replayer import Replayer
from mendwork.engine.safety.budgets import BudgetLimits
from tests.fakes.browser import FakeBrowser, FakeLauncher
from tests.fakes.clock import FakeClock
from tests.fakes.ledger import InMemoryUsageLedger
from tests.fakes.ports import (
    DictSecretResolver,
    InMemoryArtifactStore,
    RecordingEventSink,
    SequenceRandom,
    SequentialRunIds,
)
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button
from tests.unit.replay.builders import browser, config, selector
from tests.workflows import version

pytestmark = pytest.mark.asyncio

LEDGER_URL: Final = "https://ledger.example.test/ledger"
EXPORT: Final = export_button(selectors=[EXPORT_TEST_ID.model_dump()])
PRINT_TEST_ID: Final = selector(strategy="test_id", value="ledger-print")
PRINT: Final = export_button(
    accessible_name="Print ledger",
    text="Print ledger",
    attributes={"id": "print-ledger", "data_testid": "ledger-print", "type": "button"},
    bbox={"x": 0.8, "y": 0.3, "width": 0.12, "height": 0.05},
    selectors=[PRINT_TEST_ID.model_dump()],
)
FAR: Final = Box(x=0.0, y=0.9, width=0.1, height=0.05)
NOW: Final = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def reply(choice: int | None) -> str:
    return json.dumps({"choice": choice, "confidence": 0.8, "reason": "It exports the ledger."})


def says(text: str) -> Callable[[FakeBrowser], None]:
    def effect(page: FakeBrowser) -> None:
        page.text = text

    return effect


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
    *,
    fingerprint: object = EXPORT,
    risk: str = "safe",
    done: str = "Ledger exported",
) -> dict[str, object]:
    assert hasattr(fingerprint, "model_dump")
    return {
        "id": step_id,
        "intent": f"Click {step_id}",
        "action": "click",
        "risk": risk,
        "target": fingerprint.model_dump(mode="json"),
        "checkpoints": [{"kind": "text_present", "text": done}],
    }


def renamed(page: FakeBrowser, key: str = "renamed", **options: object) -> None:
    """The export button renamed and given a new id, keeping its test id: below the threshold."""
    add_element(
        page,
        key,
        EXPORT,
        identity={"name": "Quarterly download"},
        facts={"text": "Quarterly download", "id": "quarterly-download"},
        **options,  # type: ignore[arg-type]  # add_element's own keyword options
    )


def summary(page: FakeBrowser, key: str = "summary", **options: object) -> None:
    add_element(
        page,
        key,
        EXPORT,
        identity={"name": "Export summary"},
        facts={
            "text": "Export summary",
            "id": None,
            "data_testid": None,
            "nearby_text": ("Quarterly ledger",),
            "box": FAR,
        },
        **options,  # type: ignore[arg-type]  # add_element's own keyword options
    )


async def replay(
    page: FakeBrowser,
    model: FakeModel,
    *steps: dict[str, object],
    ledger: InMemoryUsageLedger | None = None,
) -> tuple[Run, RecordingEventSink]:
    events = RecordingEventSink()
    rung = ModelRung(
        model=model,
        config=ModelChoiceConfig(
            candidates_k=5, timeout_ms=5_000, provider="fake", model="scripted"
        ),
        limits=BudgetLimits(per_run=4, per_day=200),
        ledger=ledger or InMemoryUsageLedger(),
    )
    replayer = Replayer(
        launcher=FakeLauncher(page),
        artifacts=InMemoryArtifactStore(),
        events=events,
        secrets=DictSecretResolver({}),
        clock=FakeClock(NOW),
        timer=page.timer,
        randomness=SequenceRandom([0.0]),
        run_ids=SequentialRunIds(),
        config=config(),
        model=rung,
    )
    return await replayer.run(version(steps=list(steps)), {}), events


def result(run: Run, step_id: str) -> StepResult:
    return next(item for item in run.steps if item.step_id == step_id)


def finished(events: RecordingEventSink) -> RunFinishedEvent:
    [event] = [event for event in events.events if isinstance(event, RunFinishedEvent)]
    return event


async def test_a_run_whose_steps_resolve_makes_no_model_call() -> None:
    page = browser()
    add_element(page, "export", EXPORT, on_action=says("Ledger exported"))
    page.finds[EXPORT_TEST_ID] = "export"
    ledger = InMemoryUsageLedger()
    model = FakeModel([reply(1)])

    run, events = await replay(page, model, open_ledger(), click(), ledger=ledger)

    assert run.status is RunStatus.SUCCEEDED
    assert model.requests == []
    assert ledger.reservations == 0
    assert run.model_usage == ModelUsageTotals()
    assert finished(events).model_usage == ModelUsageTotals()


async def test_a_model_heal_is_acted_on_verified_and_its_usage_totalled() -> None:
    page = browser()
    renamed(page, on_action=says("Ledger exported"))
    summary(page)
    ledger = InMemoryUsageLedger()
    model = FakeModel([reply(1)], input_tokens=612, output_tokens=38)

    run, events = await replay(page, model, open_ledger(), click(), ledger=ledger)

    step = result(run, "export")
    assert run.status is RunStatus.SUCCEEDED
    assert page.calls_named("click") == ["click:renamed"]
    types = [event.type for event in events.events if getattr(event, "step_id", None) == "export"]
    assert types == [
        "step_started", "heal_attempted", "heal_attempted", "heal_attempted", "heal_attempted",
        "target_resolved", "action_performed", "checkpoint_passed", "heal_verified",
        "step_succeeded",
    ]  # fmt: skip
    assert step.heal is not None
    assert step.heal.healed_rung == 3
    assert [(report.rung, report.verification) for report in step.heal.attempts] == [
        (0, Verification.NOT_PERFORMED),
        (1, Verification.NOT_PERFORMED),
        (2, Verification.NOT_PERFORMED),
        (3, Verification.PASSED),
    ]
    assert step.target is not None
    assert step.target.healed_rung == 3
    [verified] = [event for event in events.events if isinstance(event, HealVerifiedEvent)]
    assert (verified.rung, verified.passed) == (3, True)
    assert run.model_usage == ModelUsageTotals(calls=1, input_tokens=612, output_tokens=38)
    assert finished(events).model_usage == run.model_usage
    assert ledger.calls == {date(2026, 9, 13): 1}


async def test_a_model_heal_that_fails_its_checkpoints_is_excluded_and_the_model_asked_again() -> (
    None
):
    page = browser()
    renamed(page)
    summary(page, on_action=says("Ledger exported"))
    model = FakeModel([reply(1), reply(1)])

    run, _ = await replay(page, model, open_ledger(), click())

    step = result(run, "export")
    assert run.status is RunStatus.SUCCEEDED
    assert page.calls_named("click") == ["click:renamed", "click:summary"]
    assert len(model.requests) == 2
    assert [item.description.name for item in model.requests[1].shown] == ["Export summary"]
    assert step.heal is not None
    assert [r.verification for r in step.heal.attempts if r.rung == 3] == [
        Verification.FAILED,
        Verification.PASSED,
    ]
    assert run.model_usage.calls == 2


async def test_an_irreversible_model_heal_waits_for_approval_with_the_models_evidence() -> None:
    page = browser()
    renamed(page, on_action=says("Ledger exported"))
    model = FakeModel([reply(1)])

    run, _ = await replay(page, model, open_ledger(), click(risk="irreversible"))

    step = result(run, "export")
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert step.status is StepStatus.AWAITING_APPROVAL
    assert page.calls_named("click") == []
    assert step.heal is not None
    proposal = step.heal.proposal
    assert proposal is not None
    assert proposal.rung == 3
    assert proposal.model is not None
    assert [item.description.name for item in proposal.model.shown] == ["Quarterly download"]


async def test_a_restore_replays_an_earlier_model_heal_without_asking_the_model_again() -> None:
    page = browser()
    renamed(page, on_action=says("Ledger exported"))
    add_element(page, "print_old", PRINT)
    add_element(
        page,
        "print_new",
        PRINT,
        identity={"name": "Print the ledger"},
        facts={"text": "Print the ledger", "id": "print-2"},
        on_action=says("Ledger printed"),
    )
    model = FakeModel([reply(1)])
    printed: Mapping[str, object] = click("print", fingerprint=PRINT, done="Ledger printed")

    run, _ = await replay(page, model, open_ledger(), click(), dict(printed))

    assert run.status is RunStatus.SUCCEEDED, run.error
    assert page.calls_named("click") == [
        "click:renamed", "click:print_old", "click:renamed", "click:print_new",
    ]  # fmt: skip
    assert len(model.requests) == 1
    heal = result(run, "print").heal
    assert heal is not None
    [recovery] = heal.recoveries
    assert (recovery.restored, recovery.replayed) == (True, ("export",))
