"""Interrupting a run: where it stops, what it records, and the status it ends with (ADR 0011).

A browser that parks at one call lets a test cancel the run's task at exactly that moment, the way
a first Ctrl+C does, without any waiting on time.
"""

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

import pytest

from mendwork.engine.domain.events import StepFailedEvent
from mendwork.engine.domain.runs import (
    ArtifactName,
    Run,
    RunId,
    RunStatus,
    StepStatus,
    parse_run_id,
)
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.ports.browser_types import ElementRef, NavigationOutcome, UniqueMatch
from mendwork.engine.replay.replayer import Replayer
from tests.fakes.browser import FakeBrowser, FakeLauncher
from tests.fakes.clock import FakeClock
from tests.fakes.egress import TEST_POLICY, FakeResolver
from tests.fakes.ports import (
    DictSecretResolver,
    InMemoryArtifactStore,
    InMemoryRunRecords,
    RecordingEventSink,
    SequenceRandom,
    SequentialRunIds,
)
from tests.fakes.timer import FakeTimer
from tests.unit.replay.builders import button, config, selector
from tests.workflows import CREATED_AT_DATETIME, fingerprint, literal, version

pytestmark = pytest.mark.asyncio

RUN_ID: Final = parse_run_id("20260911T000000Z-00000001")
PORTAL: Final = "https://portal.example.test/orders"
EXPORT_ID: Final = selector(strategy="test_id", value="export")
SHARE_ID: Final = selector(strategy="test_id", value="share")


def says(text: str) -> "Callable[[FakeBrowser], None]":
    def effect(page: FakeBrowser) -> None:
        page.text = text

    return effect


def open_step() -> dict[str, object]:
    return {
        "id": "open",
        "intent": "Open the orders page",
        "action": "navigate",
        "risk": "safe",
        "value": literal(PORTAL),
    }


def click_step(step_id: str, found: Selector, done: str, risk: str = "safe") -> dict[str, object]:
    return {
        "id": step_id,
        "intent": f"Click the '{step_id}' button",
        "action": "click",
        "risk": risk,
        "target": fingerprint(accessible_name=step_id.title(), selectors=[found.model_dump()]),
        "checkpoints": [{"kind": "text_present", "text": done}],
    }


@dataclass
class ParkingBrowser(FakeBrowser):
    """A fake page that stops forever at one call, and notes the record writes before a click."""

    park_on: str = ""
    """``navigate``, ``resolve:<test id>``, or ``click``."""
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    store: InMemoryArtifactStore | None = None
    writes_before_click: int | None = None

    async def navigate(self, url: str, *, timeout_ms: int) -> NavigationOutcome:
        if self.park_on == "navigate":
            await self._park()
        return await super().navigate(url, timeout_ms=timeout_ms)

    async def resolve_unique(self, selector: Selector) -> UniqueMatch:
        if {"resolve:export": EXPORT_ID, "resolve:share": SHARE_ID}.get(self.park_on) == selector:
            await self._park()
        return await super().resolve_unique(selector)

    async def click(self, element: ElementRef, *, timeout_ms: int) -> None:
        if self.store is not None:
            self.writes_before_click = len(self.store.writes)
        if self.park_on == "click":
            await self._park()
        await super().click(element, timeout_ms=timeout_ms)

    async def _park(self) -> None:
        self.reached.set()
        await asyncio.Event().wait()


def page(park_on: str) -> ParkingBrowser:
    return ParkingBrowser(
        timer=FakeTimer(),
        elements={
            "export": button("Export", on_action=says("Exported")),
            "share": button("Share", on_action=says("Shared")),
        },
        finds={EXPORT_ID: "export", SHARE_ID: "share"},
        park_on=park_on,
    )


@dataclass
class Outcome:
    run: Run
    events: RecordingEventSink
    store: InMemoryArtifactStore
    records: InMemoryRunRecords
    launcher: FakeLauncher


def replayer(
    browser: FakeBrowser, store: InMemoryArtifactStore, **ports: object
) -> tuple[Replayer, RecordingEventSink, InMemoryRunRecords, FakeLauncher]:
    events = RecordingEventSink()
    records = InMemoryRunRecords(store)
    launcher = FakeLauncher(browser)
    options: dict[str, object] = {
        "launcher": launcher,
        "artifacts": store,
        "records": records,
        "events": events,
        "secrets": DictSecretResolver({}),
        "clock": FakeClock(CREATED_AT_DATETIME),
        "timer": browser.timer,
        "randomness": SequenceRandom([0.0]),
        "run_ids": SequentialRunIds(),
        "config": config(),
        "egress": TEST_POLICY,
        "resolver": FakeResolver(),
        **ports,
    }
    return Replayer(**options), events, records, launcher  # type: ignore[arg-type]  # the test's own port table


async def interrupted(browser: ParkingBrowser, flow: WorkflowVersion) -> Outcome:
    store = InMemoryArtifactStore()
    browser.store = store
    subject, events, records, launcher = replayer(browser, store)
    task = asyncio.ensure_future(subject.run(flow, {}))
    await browser.reached.wait()
    assert records.held == {RUN_ID}

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    return Outcome(store.run_record(RUN_ID), events, store, records, launcher)


async def test_an_interrupt_before_any_action_cancels_the_run_and_records_its_step() -> None:
    outcome = await interrupted(
        page("resolve:export"), version(steps=[click_step("export", EXPORT_ID, "Exported")])
    )

    run = outcome.run
    step = run.steps[0]
    assert run.status is RunStatus.CANCELLED
    assert (step.status, step.action_performed, step.action_outcome_unknown) == (
        StepStatus.CANCELLED,
        False,
        False,
    )
    assert run.error is not None
    assert run.error.context == {
        "reason": "interrupted",
        "interruption": "interrupt",
        "irreversible_steps": [],
    }
    assert outcome.events.types[-3:] == ["step_started", "step_failed", "run_finished"]
    failed = outcome.events.events[-2]
    assert isinstance(failed, StepFailedEvent)
    assert failed.status is StepStatus.CANCELLED
    assert run.last_event_sequence == outcome.events.events[-1].sequence
    assert outcome.records.held == set()
    assert outcome.launcher.closed == [RUN_ID]


async def test_an_interrupt_after_a_safe_action_cancels_the_run() -> None:
    flow = version(steps=[open_step(), click_step("export", EXPORT_ID, "Exported")])

    outcome = await interrupted(page("resolve:export"), flow)

    assert outcome.run.status is RunStatus.CANCELLED
    assert [step.status for step in outcome.run.steps] == [
        StepStatus.SUCCEEDED,
        StepStatus.CANCELLED,
    ]


async def test_an_interrupt_while_an_action_is_sent_leaves_its_outcome_unknown() -> None:
    flow = version(steps=[open_step(), click_step("export", EXPORT_ID, "Exported")])

    outcome = await interrupted(page("click"), flow)

    step = outcome.run.steps[1]
    assert outcome.run.status is RunStatus.CANCELLED
    assert (step.action_performed, step.action_outcome_unknown) == (False, True)


async def test_an_irreversible_dispatch_is_journaled_before_it_is_sent_and_needs_review() -> None:
    browser = page("click")
    flow = version(steps=[open_step(), click_step("export", EXPORT_ID, "Exported", "irreversible")])

    outcome = await interrupted(browser, flow)

    run = outcome.run
    written = outcome.store.record_writes(RUN_ID)
    first_journaled = next(
        index for index, record in enumerate(written) if record.irreversible_dispatched
    )
    record_positions = [
        position for position, (_, name, _) in enumerate(outcome.store.writes) if name == "run.json"
    ]
    assert browser.writes_before_click is not None
    assert record_positions[first_journaled] < browser.writes_before_click
    assert run.status is RunStatus.NEEDS_REVIEW
    assert [(item.step_id, item.index, item.segment) for item in run.irreversible_dispatched] == [
        ("export", 1, 1)
    ]
    assert run.error is not None
    assert run.error.context["irreversible_steps"] == ["export"]


async def test_an_irreversible_step_that_succeeded_still_makes_a_later_interrupt_need_review() -> (
    None
):
    flow = version(
        steps=[
            click_step("export", EXPORT_ID, "Exported", "irreversible"),
            click_step("share", SHARE_ID, "Shared"),
        ]
    )

    outcome = await interrupted(page("resolve:share"), flow)

    assert outcome.run.status is RunStatus.NEEDS_REVIEW
    assert [step.status for step in outcome.run.steps] == [
        StepStatus.SUCCEEDED,
        StepStatus.CANCELLED,
    ]


async def test_an_interrupt_during_preflight_creates_nothing() -> None:
    class ParkingResolver(FakeResolver):
        def __init__(self) -> None:
            super().__init__()
            self.reached = asyncio.Event()

        async def resolve(self, host: str, *, timeout_ms: int) -> tuple[str, ...]:
            self.reached.set()
            await asyncio.Event().wait()
            return ()

    resolver = ParkingResolver()
    store = InMemoryArtifactStore()
    subject, events, records, launcher = replayer(page(""), store, resolver=resolver)
    task = asyncio.ensure_future(subject.run(version(steps=[open_step()]), {}))
    await resolver.reached.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (store.files, events.events, records.claimed, launcher.sessions) == ({}, [], [], [])


async def test_an_interrupt_while_the_run_is_created_records_it_cancelled_before_any_step() -> None:
    class ParkingStore(InMemoryArtifactStore):
        def __init__(self) -> None:
            super().__init__()
            self.reached = asyncio.Event()

        async def write(self, run_id: RunId, name: ArtifactName, data: bytes) -> ArtifactName:
            if name == "run.json" and not self.reached.is_set():
                self.reached.set()
                await asyncio.Event().wait()
            return await super().write(run_id, name, data)

    store = ParkingStore()
    subject, events, records, launcher = replayer(page(""), store)
    task = asyncio.ensure_future(subject.run(version(steps=[open_step()]), {}))
    await store.reached.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run = store.run_record(RUN_ID)
    assert run.status is RunStatus.CANCELLED
    assert [step.status for step in run.steps] == [StepStatus.NOT_RUN]
    assert (events.events, launcher.sessions, records.held) == ([], [], set())


async def test_the_record_is_journaled_after_every_step_beside_the_exact_workflow_it_runs() -> None:
    store = InMemoryArtifactStore()
    flow = version(steps=[open_step(), click_step("export", EXPORT_ID, "Exported")])
    subject, events, _records, _launcher = replayer(page(""), store)

    run = await subject.run(flow, {})

    statuses = [
        (record.status, [step.status for step in record.steps])
        for record in store.record_writes(RUN_ID)
    ]
    assert statuses == [
        (RunStatus.RUNNING, [StepStatus.NOT_RUN, StepStatus.NOT_RUN]),
        (RunStatus.RUNNING, [StepStatus.SUCCEEDED, StepStatus.NOT_RUN]),
        (RunStatus.RUNNING, [StepStatus.SUCCEEDED, StepStatus.SUCCEEDED]),
        (RunStatus.SUCCEEDED, [StepStatus.SUCCEEDED, StepStatus.SUCCEEDED]),
    ]
    snapshot = store.files[(RUN_ID, ArtifactName("workflow.json"))]
    assert run.workflow_sha256 == hashlib.sha256(snapshot).hexdigest()
    assert WorkflowVersion.model_validate_json(snapshot) == flow
    assert run.last_event_sequence == events.events[-1].sequence
    assert (run.segments[0].kind, run.segments[0].egress_allowed_domains) == (
        "run",
        TEST_POLICY.allowed_domains,
    )
    assert run.segments[0].duration_ms == run.duration_ms
