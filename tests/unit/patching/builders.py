"""A ledger whose export button was renamed: one verified Rung 2 heal on fakes, for patching tests.

Nothing here comes from the chaos portal. The recorded button is "Export ledger" with test id
``ledger-export``. The renamed page keeps the test id and calls the button "Share ledger", so Rung 0
finds a drifted match, Rung 2 accepts it, and the text "Ledger exported" proves the heal. The healed
element's only selector that still finds exactly it is the test id, so its fingerprint is the
recorded one with the new name.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from mendwork.engine.domain.enums import CheckpointKind, PromotionPolicy, RiskLevel
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import HealAttemptReport, HealReport, RungOutcome, Verification
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.patches import FoundTarget, NotSavedReason, WorkflowSource
from mendwork.engine.domain.runs import (
    CheckpointResult,
    Run,
    RunStatus,
    StepResult,
    StepStatus,
    parse_run_id,
)
from mendwork.engine.domain.workflow import WorkflowContent, WorkflowVersion
from mendwork.engine.errors import VersionConflict, WorkflowStoreUnavailable
from mendwork.engine.patching.config import PatchingConfig
from mendwork.engine.patching.patcher import Patcher
from mendwork.engine.ports.pending_patches import PendingPatches
from mendwork.engine.ports.workflow_store import WorkflowStore
from mendwork.engine.replay.replayer import Replayer
from tests.fakes.browser import FakeBrowser, FakeLauncher
from tests.fakes.clock import FakeClock
from tests.fakes.egress import TEST_POLICY, FakeResolver
from tests.fakes.pending_patches import InMemoryPendingPatches
from tests.fakes.ports import (
    DictSecretResolver,
    InMemoryArtifactStore,
    InMemoryRunRecords,
    RecordingEventSink,
    SequenceRandom,
    SequentialRunIds,
)
from tests.fakes.workflow_store import InMemoryWorkflowStore
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button
from tests.unit.replay.builders import browser, config
from tests.workflows import CREATED_AT_DATETIME, version

WORKFLOW_ID: Final = WorkflowId("ledger")
LEDGER_URL: Final = "https://ledger.example.test/ledger"
EXPORTED: Final = "Ledger exported"
RECORDED_NAME: Final = "Export ledger"
RENAMED: Final = "Share ledger"
EXPORT: Final = export_button(selectors=[EXPORT_TEST_ID.model_dump()])
SAVES: Final = WorkflowSource(path="ledger.yaml", stored_version=1, saves_heals=True)
DIFFERS: Final = WorkflowSource(
    path="ledger.yaml", saves_heals=False, not_saved=NotSavedReason.FILE_DIFFERS
)
IMMEDIATE: Final = PatchingConfig(
    promotion=PromotionPolicy.IMMEDIATE, successes_required=2, publish_attempts=3
)
AFTER_TWO: Final = PatchingConfig(
    promotion=PromotionPolicy.AFTER_N_SUCCESSES, successes_required=2, publish_attempts=3
)
CRAFTED_RUN: Final = parse_run_id("20260915T120000Z-0000beef")


def ledger_steps(*, risk: str = "safe", target: Fingerprint = EXPORT) -> list[dict[str, object]]:
    """Open the ledger, then export it; the export is proven by the text it shows."""
    return [
        {
            "id": "open",
            "intent": "Open the ledger",
            "action": "navigate",
            "risk": "safe",
            "value": {"kind": "literal", "value": LEDGER_URL},
        },
        {
            "id": "export",
            "intent": "Click the 'Export ledger' button",
            "action": "click",
            "risk": risk,
            "target": target.model_dump(mode="json"),
            "checkpoints": [{"kind": "text_present", "text": EXPORTED}],
        },
    ]


def ledger_version(**overrides: object) -> WorkflowVersion:
    """Version 1 of the ledger workflow."""
    values: dict[str, object] = {"workflow_id": WORKFLOW_ID, "steps": ledger_steps()}
    return version(**{**values, **overrides})


def says(text: str) -> Callable[[FakeBrowser], None]:
    def effect(page: FakeBrowser) -> None:
        page.text = text

    return effect


def ledger_page(name: str = RENAMED, *, exports: bool = True) -> FakeBrowser:
    """The ledger page with its export button called ``name``; the recorded test id finds it."""
    page = browser()
    add_element(
        page,
        "export",
        EXPORT,
        identity={"name": name},
        facts={"text": name},
        on_action=says(EXPORTED) if exports else None,
    )
    page.finds[EXPORT_TEST_ID] = "export"
    return page


def with_intent(workflow: WorkflowVersion, step_id: str, intent: str) -> WorkflowContent:
    """The version's content with one step's intent changed."""
    steps = tuple(
        step.model_copy(update={"intent": intent}) if step.id == step_id else step
        for step in workflow.steps
    )
    return WorkflowContent(inputs=workflow.inputs, secrets=workflow.secrets, steps=steps)


def result(run: Run, step_id: str) -> StepResult:
    """The run's result for a step."""
    return next(item for item in run.steps if item.step_id == step_id)


def with_found(run: Run, step_id: str, fingerprint: Fingerprint) -> Run:
    """The run as if its heal of the step had captured this fingerprint."""
    steps = tuple(
        item.model_copy(update={"found": FoundTarget(fingerprint=fingerprint)})
        if item.step_id == step_id
        else item
        for item in run.steps
    )
    return run.model_copy(update={"steps": steps})


def crafted_run(
    workflow: WorkflowVersion,
    healed: str,
    found: Fingerprint,
    *,
    source: WorkflowSource = SAVES,
    status: RunStatus = RunStatus.SUCCEEDED,
) -> Run:
    """A finished run of any workflow in which one step was healed at Rung 2 and verified."""
    steps: list[StepResult] = []
    for index, step in enumerate(workflow.steps):
        is_healed = step.id == healed
        steps.append(
            StepResult(
                step_id=step.id,
                index=index,
                action=step.action,
                status=StepStatus.SUCCEEDED,
                heal=_verified_report() if is_healed else None,
                found=FoundTarget(fingerprint=found) if is_healed else None,
                checkpoints=tuple(
                    CheckpointResult(index=position, kind=CheckpointKind(item.kind), passed=True)
                    for position, item in enumerate(step.checkpoints)
                )
                if is_healed
                else (),
            )
        )
    return Run(
        run_id=CRAFTED_RUN,
        workflow_id=workflow.workflow_id,
        workflow_version=workflow.version,
        status=status,
        started_at=datetime(2026, 9, 15, 12, tzinfo=UTC),
        steps=tuple(steps),
        source=source,
    )


def _verified_report() -> HealReport:
    return HealReport(
        attempts=(
            HealAttemptReport(
                rung=2,
                attempt=1,
                outcome=RungOutcome.RESOLVED,
                score=0.85,
                margin=0.63,
                threshold=0.6,
                required_margin=0.15,
                verification=Verification.PASSED,
            ),
        ),
        healed_rung=2,
    )


class ScriptedStore:
    """A workflow store that can do something just before a publish, conflict, or be unavailable."""

    def __init__(
        self,
        inner: InMemoryWorkflowStore,
        *,
        before_publish: Callable[[], Awaitable[None]] | None = None,
        conflicts: bool = False,
        unavailable: bool = False,
        log: list[str] | None = None,
    ) -> None:
        self.inner = inner
        self.before_publish = before_publish
        self.conflicts = conflicts
        self.unavailable = unavailable
        self.log: list[str] = log if log is not None else []
        self.calls = 0

    async def publish(self, version: WorkflowVersion) -> None:
        self.calls += 1
        self._check()
        hook, self.before_publish = self.before_publish, None
        if hook is not None:
            await hook()
        if self.conflicts:
            raise VersionConflict("another version was published first", reason="exists")
        await self.inner.publish(version)
        self.log.append(f"publish:v{version.version}")

    async def get(self, workflow_id: WorkflowId, number: int) -> WorkflowVersion | None:
        self.calls += 1
        self._check()
        return await self.inner.get(workflow_id, number)

    async def latest(self, workflow_id: WorkflowId) -> WorkflowVersion | None:
        self.calls += 1
        self._check()
        return await self.inner.latest(workflow_id)

    async def versions(self, workflow_id: WorkflowId) -> tuple[int, ...]:
        self.calls += 1
        self._check()
        return await self.inner.versions(workflow_id)

    def _check(self) -> None:
        if self.unavailable:
            raise WorkflowStoreUnavailable("the workflow store is unavailable on purpose")


@dataclass
class Ledger:
    """A workflow store holding the ledger's version 1, pending patches, and the run fakes."""

    store: InMemoryWorkflowStore = field(default_factory=InMemoryWorkflowStore)
    pending: InMemoryPendingPatches = field(default_factory=InMemoryPendingPatches)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    clock: FakeClock = field(default_factory=lambda: FakeClock(CREATED_AT_DATETIME))
    run_ids: SequentialRunIds = field(default_factory=SequentialRunIds)
    events: RecordingEventSink = field(default_factory=RecordingEventSink)

    def patcher(
        self,
        patching: PatchingConfig = IMMEDIATE,
        *,
        store: WorkflowStore | None = None,
        pending: PendingPatches | None = None,
    ) -> Patcher:
        return Patcher(
            store=store if store is not None else self.store,
            pending=pending if pending is not None else self.pending,
            clock=self.clock,
            config=patching,
        )

    async def run(
        self,
        page: FakeBrowser,
        workflow: WorkflowVersion,
        *,
        source: WorkflowSource | None = SAVES,
        patcher: Patcher | None = None,
    ) -> Run:
        """One run of the workflow on the page, as ``mendwork run`` would do it."""
        replayer = Replayer(
            launcher=FakeLauncher(page),
            artifacts=self.artifacts,
            events=self.events,
            secrets=DictSecretResolver({}),
            clock=self.clock,
            timer=page.timer,
            randomness=SequenceRandom([0.0]),
            run_ids=self.run_ids,
            config=config(),
            egress=TEST_POLICY,
            resolver=FakeResolver(),
            records=InMemoryRunRecords(),
            patcher=patcher,
        )
        return await replayer.run(workflow, {}, source=source)

    async def latest(self) -> WorkflowVersion:
        found = await self.store.latest(WORKFLOW_ID)
        assert found is not None
        return found


async def ledger() -> Ledger:
    """A ledger whose store holds version 1."""
    made = Ledger()
    await made.store.publish(ledger_version())
    return made


def irreversible_ledger() -> WorkflowVersion:
    """The ledger workflow with its export raised to irreversible."""
    workflow = ledger_version(steps=ledger_steps(risk="irreversible"))
    assert workflow.steps[1].risk is RiskLevel.IRREVERSIBLE
    return workflow
