"""Approving and rejecting proposals (ADR 0011).

The audit log is written first; a resume rebuilds the page in a new browser and acts only on the
approved element, matched on what it is and not where it sits; and a page that changed fails the
run with nothing acted on, never a fresh proposal stretched from an old approval.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final

import pytest

from mendwork.adapters.models.fake import FakeModel
from mendwork.engine.domain.approvals import DecisionKind, ProposalOutcome, StaleReason
from mendwork.engine.domain.audit import AuditDraft, AuditEntry, AuditKind
from mendwork.engine.domain.events import RunEvent
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.runs import (
    ArtifactName,
    Run,
    RunId,
    RunSegmentKind,
    RunStatus,
    StepStatus,
)
from mendwork.engine.errors import (
    EgressBlocked,
    ProposalNotPending,
    RunBusy,
    RunNotResumable,
)
from mendwork.engine.healing.model_rung import ModelChoiceConfig, ModelRung
from mendwork.engine.ports.browser_types import NavigationOutcome
from mendwork.engine.ports.element_types import Box
from mendwork.engine.replay.decisions import ApprovalDesk
from mendwork.engine.replay.journal import encode_run
from mendwork.engine.replay.replayer import Replayer
from mendwork.engine.replay.resume import Resumer
from mendwork.engine.replay.run_execution import ExecutionPorts
from mendwork.engine.safety.budgets import BudgetLimits
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.redaction import REDACTED
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.audit import InMemoryAuditLog
from tests.fakes.browser import Effect, FakeBrowser, FakeLauncher
from tests.fakes.clock import FakeClock
from tests.fakes.egress import TEST_POLICY, FakeResolver
from tests.fakes.ledger import InMemoryUsageLedger
from tests.fakes.ports import (
    DictSecretResolver,
    InMemoryArtifactStore,
    InMemoryRunRecords,
    RecordingEventSink,
    SequenceRandom,
    SequentialRunIds,
)
from tests.fakes.timer import FakeTimer
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button
from tests.unit.healing.rung3_builders import RECORDED, renamed, reply, summary
from tests.unit.replay.builders import config, selector
from tests.workflows import CREATED_AT_DATETIME, version

pytestmark = pytest.mark.asyncio

LEDGER_URL: Final = "https://ledger.example.test/ledger"
EXPORT: Final = export_button(selectors=[EXPORT_TEST_ID.model_dump()])
EXPORTED: Final = "Ledger exported"
PROPOSAL: Final = "export-1"
MENU_ID: Final = selector(strategy="test_id", value="ledger-menu")
MENU: Final = export_button(
    accessible_name="Open menu",
    text="Open menu",
    attributes={"id": "ledger-menu", "data_testid": "ledger-menu", "type": "button"},
    nearby_text=["Ledger tools"],
    structural_path="header > nav > button",
    bbox={"x": 0.05, "y": 0.02, "width": 0.1, "height": 0.04},
    selectors=[MENU_ID.model_dump()],
)
SUBMIT_ID: Final = selector(strategy="test_id", value="ledger-submit")
PUSHED_DOWN: Final = Box(x=0.6, y=0.42, width=0.12, height=0.05)
"""Where the export button sits once a cookie banner above it takes 12% of the page."""


def says(text: str) -> Effect:
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
    step_id: str, target: object, done: str = EXPORTED, risk: str = "irreversible"
) -> dict[str, object]:
    return {
        "id": step_id,
        "intent": f"Click {step_id}",
        "action": "click",
        "risk": risk,
        "target": target,
        "checkpoints": [{"kind": "text_present", "text": done}],
    }


def export(target: object = None) -> dict[str, object]:
    return click("export", target or EXPORT.model_dump(mode="json"))


def menu() -> dict[str, object]:
    return click("menu", MENU.model_dump(mode="json"), done="Menu open", risk="safe")


def ledger_page(page: FakeBrowser | None = None, *, exports: bool = True) -> FakeBrowser:
    """A ledger page whose export button no recorded selector finds any more."""
    ledger = page or FakeBrowser(timer=FakeTimer())
    add_element(ledger, "export", EXPORT, on_action=says(EXPORTED) if exports else None)
    return ledger


def model_rung(model: FakeModel) -> ModelRung:
    return ModelRung(
        model=model,
        config=ModelChoiceConfig(
            candidates_k=5, timeout_ms=5_000, provider="fake", model="scripted"
        ),
        limits=BudgetLimits(per_run=4, per_day=200),
        ledger=InMemoryUsageLedger(),
    )


@dataclass
class Harness:
    """One artifacts store, audit log, and page, shared by the paused run and the decisions."""

    page: FakeBrowser
    audit: InMemoryAuditLog = field(default_factory=InMemoryAuditLog)
    events: RecordingEventSink = field(default_factory=RecordingEventSink)
    secrets: DictSecretResolver = field(default_factory=lambda: DictSecretResolver({}))
    store: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    clock: FakeClock = field(default_factory=lambda: FakeClock(CREATED_AT_DATETIME))
    launcher: FakeLauncher = field(init=False)
    records: InMemoryRunRecords = field(init=False)

    def __post_init__(self) -> None:
        self.launcher = FakeLauncher(self.page)
        self.records = InMemoryRunRecords(self.store)

    async def pause(self, *steps: dict[str, object], model: ModelRung | None = None) -> Run:
        replayer = Replayer(
            launcher=self.launcher,
            artifacts=self.store,
            records=self.records,
            events=self.events,
            secrets=self.secrets,
            clock=self.clock,
            timer=self.page.timer,
            randomness=SequenceRandom([0.0]),
            run_ids=SequentialRunIds(),
            config=config(),
            egress=TEST_POLICY,
            resolver=FakeResolver(),
            model=model,
        )
        run = await replayer.run(version(steps=list(steps)), {})
        assert run.status is RunStatus.AWAITING_APPROVAL
        self.clock.advance(timedelta(minutes=5))
        self.page.calls.clear()
        return run

    def desk(self) -> ApprovalDesk:
        return ApprovalDesk(
            records=self.records,
            audit=self.audit,
            artifacts=self.store,
            secrets=self.secrets,
            clock=self.clock,
            scrubber=SecretScrubber(),
        )

    def resumer(
        self, *, model: ModelRung | None = None, egress: EgressPolicy = TEST_POLICY
    ) -> Resumer:
        ports = ExecutionPorts(
            launcher=self.launcher,
            artifacts=self.store,
            secrets=self.secrets,
            clock=self.clock,
            timer=self.page.timer,
            randomness=SequenceRandom([0.0]),
            config=config(),
            egress=egress,
        )
        return Resumer(ports=ports, events=self.events, resolver=FakeResolver(), model=model)

    async def approve(
        self,
        run_id: RunId,
        *,
        model: ModelRung | None = None,
        egress: EgressPolicy = TEST_POLICY,
    ) -> Run:
        return await self.desk().approve(run_id, PROPOSAL, self.resumer(model=model, egress=egress))

    def record(self, run: Run) -> Run:
        return self.store.run_record(run.run_id)


async def test_an_approval_resumes_the_run_and_acts_on_the_approved_element() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    proposal = paused.proposals[0].proposal

    finished = await harness.approve(paused.run_id)

    assert (proposal.id, proposal.step_id, proposal.step_index) == (PROPOSAL, "export", 1)
    assert finished.status is RunStatus.SUCCEEDED
    assert [step.status for step in finished.steps] == [StepStatus.SUCCEEDED] * 2
    item = finished.proposals[0]
    assert item.decision is not None
    assert (item.decision.kind, item.decision.audit_sequence) == (DecisionKind.APPROVED, 1)
    assert item.outcome is ProposalOutcome.ACTED_VERIFIED
    assert harness.page.calls_named("navigate", "click") == [
        f"navigate:{LEDGER_URL}",
        "click:export",
    ]
    assert [entry.kind for entry in harness.audit.entries] == [AuditKind.PROPOSAL_APPROVED]
    assert [segment.kind for segment in finished.segments] == [
        RunSegmentKind.RUN,
        RunSegmentKind.RESUME,
    ]
    assert finished.segments[1].proposal_id == PROPOSAL
    assert [step.status for step in finished.paused_steps] == [StepStatus.AWAITING_APPROVAL]
    assert harness.record(paused) == finished


async def test_a_resume_continues_the_runs_events_and_keeps_the_paused_evidence() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())

    finished = await harness.approve(paused.run_id)

    sequences = [event.sequence for event in harness.events.events]
    assert sequences == list(range(1, len(sequences) + 1))
    assert finished.last_event_sequence == sequences[-1]
    resumed = [event for event in harness.events.events if event.type == "run_resumed"]
    assert [(event.sequence, getattr(event, "segment", None)) for event in resumed] == [
        (paused.last_event_sequence + 1, 2)
    ]
    names = harness.store.names(paused.run_id)
    assert {"steps/002_export.png", "steps/002_export.segment2.png"} <= set(names)
    assert "steps/001_open.segment2.png" not in names


class WitnessedAudit(InMemoryAuditLog):
    """An audit log that notes how many artifact writes came before each append."""

    def __init__(self, store: InMemoryArtifactStore) -> None:
        super().__init__()
        self.store = store
        self.writes_at_append: list[int] = []

    async def append(self, draft: AuditDraft) -> AuditEntry:
        self.writes_at_append.append(len(self.store.writes))
        return await super().append(draft)


async def test_the_audit_entry_is_written_before_the_record_that_says_approved() -> None:
    store = InMemoryArtifactStore()
    audit = WitnessedAudit(store)
    harness = Harness(ledger_page(), audit=audit, store=store)
    paused = await harness.pause(open_ledger(), export())

    await harness.approve(paused.run_id)

    decided_at = next(
        position
        for position, (_, name, data) in enumerate(store.writes)
        if name == "run.json"
        and any(item.decision for item in Run.model_validate_json(data).proposals)
    )
    assert audit.writes_at_append == [decided_at]


async def test_a_cookie_banner_that_moves_the_approved_element_leaves_the_approval_valid() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    proposal = paused.proposals[0].proposal
    page = harness.page
    page.facts["export"] = page.facts["export"].model_copy(update={"box": PUSHED_DOWN})
    add_element(
        page,
        "accept_cookies",
        export_button(
            accessible_name="Accept cookies",
            text="Accept cookies",
            attributes={"id": "accept-cookies", "type": "button"},
            nearby_text=["We use cookies to keep you signed in"],
            structural_path="body > div > button",
            bbox={"x": 0.4, "y": 0.02, "width": 0.2, "height": 0.08},
        ),
    )

    finished = await harness.approve(paused.run_id)

    assert proposal.box is not None
    assert (proposal.box.y, page.facts["export"].box) == (0.3, PUSHED_DOWN)
    assert finished.status is RunStatus.SUCCEEDED
    assert finished.proposals[0].outcome is ProposalOutcome.ACTED_VERIFIED
    assert page.calls_named("click") == ["click:export"]


async def test_a_recorded_target_found_again_runs_as_recorded_without_the_heal() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    harness.page.finds[EXPORT_TEST_ID] = "export"

    finished = await harness.approve(paused.run_id)

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.proposals[0].outcome is ProposalOutcome.NOT_NEEDED
    assert finished.steps[1].heal is None
    assert harness.page.calls_named("click") == ["click:export"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda page: page.facts.update(
                export=page.facts["export"].model_copy(update={"nearby_text": ("Annual ledger",)})
            ),
            StaleReason.DIFFERENT_TARGET,
        ),
        (lambda page: page.candidates.clear(), StaleReason.APPROVED_TARGET_NOT_FOUND),
    ],
)
async def test_a_page_that_no_longer_shows_the_approved_element_fails_without_acting(
    change: Effect, reason: StaleReason
) -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    change(harness.page)

    finished = await harness.approve(paused.run_id)

    step = finished.steps[1]
    assert finished.status is RunStatus.FAILED
    assert step.status is StepStatus.FAILED
    assert step.error is not None
    assert (step.error.type, step.error.context["stale_reason"]) == ("ApprovalStale", reason.value)
    assert not step.action_performed
    assert harness.page.calls_named("click") == []
    item = finished.proposals[0]
    assert (item.outcome, item.stale_reason) == (ProposalOutcome.STALE, reason)
    assert len(finished.proposals) == 1
    assert finished.irreversible_dispatched == ()


async def test_an_earlier_step_that_cannot_be_replayed_makes_the_approval_stale() -> None:
    page = ledger_page()
    add_element(page, "menu", MENU, candidate=False, on_action=says("Menu open"))
    page.finds[MENU_ID] = "menu"
    harness = Harness(page)
    paused = await harness.pause(open_ledger(), menu(), export())
    del page.finds[MENU_ID]

    finished = await harness.approve(paused.run_id)

    step = finished.steps[2]
    assert step.error is not None
    assert step.error.context["stale_reason"] == "page_not_reestablished"
    assert "step 2 menu could not be replayed: TargetNotFound" in str(step.error.context["detail"])
    assert finished.steps[1] == paused.steps[1]
    assert page.calls_named("click") == []
    assert finished.proposals[0].stale_reason is StaleReason.PAGE_NOT_REESTABLISHED


async def test_an_earlier_verified_heal_is_found_again_by_identity_as_the_page_is_rebuilt() -> None:
    page = ledger_page()
    add_element(page, "menu", MENU, on_action=says("Menu open"))
    harness = Harness(page)
    paused = await harness.pause(open_ledger(), menu(), export())
    page.facts["menu"] = page.facts["menu"].model_copy(
        update={"box": Box(x=0.05, y=0.14, width=0.1, height=0.04)}
    )

    finished = await harness.approve(paused.run_id)

    assert paused.steps[1].heal is not None
    assert paused.steps[1].heal.healed_rung == 2
    assert paused.resume is not None
    assert [heal.step_id for heal in paused.resume.verified_heals] == ["menu"]
    assert finished.status is RunStatus.SUCCEEDED
    assert page.calls_named("click") == ["click:menu", "click:export"]


async def test_an_approved_action_whose_checkpoints_fail_needs_review() -> None:
    harness = Harness(ledger_page(exports=False))
    paused = await harness.pause(open_ledger(), export())

    finished = await harness.approve(paused.run_id)

    assert finished.status is RunStatus.NEEDS_REVIEW
    assert finished.steps[1].action_performed
    assert finished.proposals[0].outcome is ProposalOutcome.ACTED_UNVERIFIED
    assert [(item.step_id, item.segment) for item in finished.irreversible_dispatched] == [
        ("export", 2)
    ]


async def test_a_models_approved_pick_is_found_again_without_asking_any_model() -> None:
    page = FakeBrowser(timer=FakeTimer())
    renamed(page)
    summary(page)
    page.elements["renamed"].on_action = says(EXPORTED)
    harness = Harness(page)
    chosen = FakeModel([reply(1)])
    paused = await harness.pause(
        open_ledger(), export(RECORDED.model_dump(mode="json")), model=model_rung(chosen)
    )
    silent = FakeModel([])

    with_model = await harness.approve(paused.run_id, model=model_rung(silent))

    assert paused.proposals[0].proposal.rung == 3
    assert len(chosen.requests) == 1
    assert silent.requests == []
    assert with_model.status is RunStatus.SUCCEEDED
    assert with_model.proposals[0].outcome is ProposalOutcome.ACTED_VERIFIED
    assert with_model.model_usage.calls == paused.model_usage.calls == 1
    assert page.calls_named("click") == ["click:renamed"]


async def test_a_models_approved_pick_resumes_with_no_model_configured() -> None:
    page = FakeBrowser(timer=FakeTimer())
    renamed(page)
    summary(page)
    page.elements["renamed"].on_action = says(EXPORTED)
    harness = Harness(page)
    paused = await harness.pause(
        open_ledger(),
        export(RECORDED.model_dump(mode="json")),
        model=model_rung(FakeModel([reply(1)])),
    )

    finished = await harness.approve(paused.run_id, model=None)

    assert finished.proposals[0].outcome is ProposalOutcome.ACTED_VERIFIED


async def test_a_rejection_is_audited_with_its_reason_scrubbed_and_fails_the_run() -> None:
    harness = Harness(ledger_page(), secrets=DictSecretResolver({"ledger_token": "tok-5150-x"}))
    paused = await harness.pause(open_ledger(), export())
    secretive = paused.model_copy(update={"secrets": ("ledger_token",)})
    await harness.store.write(paused.run_id, ArtifactName("run.json"), encode_run(secretive))

    finished = await harness.desk().reject(paused.run_id, PROPOSAL, "tok-5150-x is not ours")

    assert finished.status is RunStatus.FAILED
    assert finished.error is not None
    assert finished.error.type == "ProposalRejected"
    (entry,) = harness.audit.entries
    assert (entry.kind, entry.reason) == (AuditKind.PROPOSAL_REJECTED, f"{REDACTED} is not ours")
    assert b"tok-5150-x" not in harness.store.files[(paused.run_id, ArtifactName("run.json"))]
    assert harness.launcher.sessions == [paused.run_id]


async def test_a_rejection_reason_is_kept_when_a_secret_to_scrub_is_unavailable() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    secretive = paused.model_copy(update={"secrets": ("ledger_token",)})
    await harness.store.write(paused.run_id, ArtifactName("run.json"), encode_run(secretive))

    await harness.desk().reject(paused.run_id, PROPOSAL, "wrong account")

    assert [entry.reason for entry in harness.audit.entries] == ["wrong account"]


async def test_a_proposal_takes_one_decision_only() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    desk = harness.desk()
    await desk.approve(paused.run_id, PROPOSAL, harness.resumer())

    with pytest.raises(ProposalNotPending, match="already approved") as again:
        await desk.approve(paused.run_id, PROPOSAL, harness.resumer())
    with pytest.raises(ProposalNotPending, match="already approved"):
        await desk.reject(paused.run_id, PROPOSAL, "too late")

    assert again.value.context["reason"] == "already_decided"
    assert len(harness.audit.entries) == 1


async def test_a_run_another_process_holds_or_an_unknown_proposal_records_nothing() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    desk = harness.desk()

    harness.records.held.add(paused.run_id)
    with pytest.raises(RunBusy):
        await desk.approve(paused.run_id, PROPOSAL, harness.resumer())
    assert await desk.current(paused.run_id) == paused
    harness.records.held.discard(paused.run_id)
    with pytest.raises(ProposalNotPending, match="has no proposal export-2"):
        await desk.reject(paused.run_id, "export-2", None)

    assert harness.audit.entries == []
    assert harness.record(paused) == paused


async def test_an_irreversible_step_before_the_approved_one_refuses_the_resume() -> None:
    page = ledger_page()
    submit = export_button(
        accessible_name="Submit",
        attributes={"id": "ledger-submit", "data_testid": "ledger-submit", "type": "submit"},
        selectors=[SUBMIT_ID.model_dump()],
    )
    add_element(page, "submit", submit, candidate=False, on_action=says("Submitted"))
    page.finds[SUBMIT_ID] = "submit"
    harness = Harness(page)
    paused = await harness.pause(
        open_ledger(), click("submit", submit.model_dump(mode="json"), done="Submitted"), export()
    )

    with pytest.raises(RunNotResumable, match="step 2 submit is irreversible") as refused:
        await harness.approve(paused.run_id)

    assert refused.value.context["reason"] == "earlier_irreversible_step"
    assert harness.audit.entries == []
    assert harness.record(paused) == paused


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (lambda data: data + b"\n", "workflow_changed"),
        (lambda data: b"{}", "workflow_unreadable"),
    ],
)
async def test_a_saved_workflow_that_changed_or_cannot_be_read_refuses_the_resume(
    snapshot: object, reason: str
) -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    key = (paused.run_id, ArtifactName("workflow.json"))
    assert callable(snapshot)
    harness.store.files[key] = snapshot(harness.store.files[key])

    with pytest.raises(RunNotResumable) as refused:
        await harness.approve(paused.run_id)

    assert refused.value.context["reason"] == reason
    assert harness.audit.entries == []


async def test_a_record_from_before_resumable_runs_refuses_the_resume() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    old = paused.model_copy(update={"record_version": 1})
    await harness.store.write(paused.run_id, ArtifactName("run.json"), encode_run(old))

    with pytest.raises(RunNotResumable, match="recorded before runs could be resumed"):
        await harness.approve(paused.run_id)


async def test_todays_egress_policy_is_checked_before_the_approval_is_recorded() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())

    with pytest.raises(EgressBlocked):
        await harness.approve(
            paused.run_id, egress=EgressPolicy(allowed_domains=("elsewhere.test",))
        )

    assert harness.audit.entries == []
    assert harness.record(paused) == paused


async def test_an_approval_only_the_audit_log_holds_is_finished_and_not_repeated() -> None:
    harness = Harness(ledger_page())
    paused = await harness.pause(open_ledger(), export())
    await harness.audit.append(
        AuditDraft(
            kind=AuditKind.PROPOSAL_APPROVED,
            at=harness.clock.now(),
            run_id=paused.run_id,
            workflow_id=paused.workflow_id,
            workflow_version=paused.workflow_version,
            step_id=StepId("export"),
            step_index=1,
            proposal_id=PROPOSAL,
        )
    )
    desk = harness.desk()

    assert (await desk.current(paused.run_id)).status is RunStatus.CANCELLED
    assert harness.record(paused) == paused
    with pytest.raises(ProposalNotPending, match="already approved"):
        await desk.approve(paused.run_id, PROPOSAL, harness.resumer())

    record = harness.record(paused)
    assert record.status is RunStatus.CANCELLED
    assert record.proposals[0].outcome is ProposalOutcome.NOT_RESUMED
    assert len(harness.audit.entries) == 1
    assert harness.launcher.sessions == [paused.run_id]


class GatedAudit(InMemoryAuditLog):
    """An audit log whose next append waits until the test lets it finish."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def append(self, draft: AuditDraft) -> AuditEntry:
        self.entered.set()
        await self.release.wait()
        return await super().append(draft)


async def test_an_interrupt_while_an_approval_is_recorded_waits_for_both_writes() -> None:
    audit = GatedAudit()
    harness = Harness(ledger_page(), audit=audit)
    paused = await harness.pause(open_ledger(), export())
    task = asyncio.ensure_future(harness.approve(paused.run_id))
    await audit.entered.wait()

    task.cancel()
    audit.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = harness.record(paused)
    decision = record.proposals[0].decision
    assert decision is not None
    assert decision.kind is DecisionKind.APPROVED
    assert record.proposals[0].outcome is ProposalOutcome.NOT_RESUMED
    assert record.status is RunStatus.CANCELLED
    assert harness.launcher.sessions == [paused.run_id]
    assert harness.records.held == set()


class ParkingSink(RecordingEventSink):
    """An event sink that stops forever at the first event of one type."""

    def __init__(self, park_on: str) -> None:
        super().__init__()
        self.park_on = park_on
        self.reached = asyncio.Event()

    async def emit(self, event: RunEvent) -> None:
        if event.type == self.park_on:
            self.reached.set()
            await asyncio.Event().wait()
        await super().emit(event)


@dataclass
class ParkingPage(FakeBrowser):
    """A ledger page that stops forever at its next navigation once parked."""

    parked: bool = False
    reached: asyncio.Event = field(default_factory=asyncio.Event)

    async def navigate(self, url: str, *, timeout_ms: int) -> NavigationOutcome:
        if self.parked:
            self.reached.set()
            await asyncio.Event().wait()
        return await super().navigate(url, timeout_ms=timeout_ms)


async def test_an_interrupt_as_the_run_resumes_cancels_it_with_the_approval_interrupted() -> None:
    sink = ParkingSink("run_resumed")
    harness = Harness(ledger_page(), events=sink)
    paused = await harness.pause(open_ledger(), export())
    task = asyncio.ensure_future(harness.approve(paused.run_id))
    await sink.reached.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = harness.record(paused)
    assert record.status is RunStatus.CANCELLED
    assert [step.status for step in record.steps] == [StepStatus.SUCCEEDED, StepStatus.NOT_RUN]
    assert record.proposals[0].outcome is ProposalOutcome.INTERRUPTED


async def test_an_interrupt_while_the_page_is_rebuilt_cancels_the_run_before_the_step() -> None:
    page = ParkingPage(timer=FakeTimer())
    harness = Harness(ledger_page(page))
    paused = await harness.pause(open_ledger(), export())
    page.parked = True
    task = asyncio.ensure_future(harness.approve(paused.run_id))
    await page.reached.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = harness.record(paused)
    assert record.status is RunStatus.CANCELLED
    assert record.proposals[0].outcome is ProposalOutcome.INTERRUPTED
    assert record.segments[-1].finished_at is not None
    assert page.calls_named("click") == []
