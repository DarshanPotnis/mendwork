"""The replayer end to end on scripted ports: order, records, evidence, secrets, and failures."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from mendwork.engine.domain.enums import ValueKind
from mendwork.engine.domain.runs import (
    ErrorCategory,
    Run,
    RunStatus,
    StepStatus,
    TraceWithheldReason,
)
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import (
    ArtifactStoreUnavailable,
    BrowserUnavailable,
    InfrastructureError,
    MendworkError,
    NavigationError,
    RunInputError,
    SecretUnavailable,
    TargetNotActionable,
)
from mendwork.engine.ports.browser_types import (
    NavigationOutcome,
    SecretText,
    TraceNotSaved,
    TraceSaved,
)
from mendwork.engine.replay.replayer import Replayer
from tests.fakes.browser import Effect, FakeBrowser, FakeElement, FakeLauncher, download
from tests.fakes.clock import FakeClock
from tests.fakes.ports import (
    DictSecretResolver,
    InMemoryArtifactStore,
    RecordingEventSink,
    SequenceRandom,
    SequentialRunIds,
)
from tests.unit.replay.builders import browser, button, config
from tests.workflows import CREATED_AT_DATETIME, fingerprint, input_ref, secret_ref, version

pytestmark = pytest.mark.asyncio

SECRET = "s3cr3t-Value!"
PORTAL = "https://portal.example.test/sign-in"
INPUTS = {"portal_url": PORTAL, "account_email": "ada@example.test"}


def by_test_id(value: str) -> dict[str, str]:
    return {"strategy": "test_id", "value": value}


def workflow() -> WorkflowVersion:
    return version(
        inputs=[{"name": "portal_url", "kind": "url"}, {"name": "account_email", "kind": "text"}],
        secrets=["portal_password"],
        steps=[
            {
                "id": "open",
                "intent": "Open the portal",
                "action": "navigate",
                "risk": "safe",
                "value": input_ref("portal_url"),
                "checkpoints": [
                    {
                        "kind": "url_matches",
                        "mode": "prefix",
                        "pattern": "https://portal.example.test/",
                    }
                ],
            },
            {
                "id": "fill_email",
                "intent": "Fill the 'Email' field",
                "action": "fill",
                "risk": "caution",
                "target": fingerprint(
                    tag="input",
                    role="textbox",
                    accessible_name="Email",
                    attributes={"type": "email"},
                    selectors=[by_test_id("email")],
                ),
                "value": input_ref("account_email"),
                "checkpoints": [{"kind": "field_has_value"}],
            },
            {
                "id": "fill_password",
                "intent": "Fill the 'Password' field",
                "action": "fill",
                "risk": "caution",
                "target": {
                    "tag": "input",
                    "accessible_name": "Password",
                    "attributes": {"type": "password"},
                    "structural_path": "form > input",
                    "selectors": [by_test_id("password")],
                },
                "value": secret_ref("portal_password"),
                "checkpoints": [{"kind": "field_has_value"}],
            },
            {
                "id": "export",
                "intent": "Click the 'Export' button",
                "action": "click",
                "risk": "safe",
                "target": fingerprint(accessible_name="Export", selectors=[by_test_id("export")]),
                "checkpoints": [
                    {"kind": "no_error_banner"},
                    {"kind": "download_completed", "filename_pattern": r"report\.csv"},
                ],
            },
        ],
    )


def emit_report(page: FakeBrowser) -> None:
    page.emit_download(download("report.csv", "/browser/downloads/1"))


def page_for(
    flow: WorkflowVersion,
    *,
    on_action: Effect | None = emit_report,
    action_error: MendworkError | None = None,
) -> FakeBrowser:
    selectors = [step.target.selectors[0] for step in flow.steps[1:]]  # type: ignore[union-attr]
    exporter = button("Export", on_action=on_action, action_error=action_error)
    return browser(
        elements={
            "email": FakeElement(tag="input", role="textbox", name="Email", input_type="email"),
            "password": FakeElement(
                tag="input", role="textbox", name="Password", input_type="password"
            ),
            "export": exporter,
        },
        finds=dict(zip(selectors, ["email", "password", "export"], strict=True)),
    )


@dataclass
class Harness:
    flow: WorkflowVersion
    page: FakeBrowser
    launcher: FakeLauncher
    artifacts: InMemoryArtifactStore
    events: RecordingEventSink
    replayer: Replayer

    async def run(self, inputs: dict[str, str] = INPUTS) -> Run:
        return await self.replayer.run(self.flow, inputs)

    def stored(self, run: Run) -> Run:
        return self.artifacts.run_record(run.run_id)


def harness(
    *,
    flow: WorkflowVersion | None = None,
    page: FakeBrowser | None = None,
    secrets: dict[str, str] | None = None,
    artifacts: InMemoryArtifactStore | None = None,
    **overrides: object,
) -> Harness:
    flow = flow or workflow()
    page = page or page_for(flow)
    launcher = FakeLauncher(page)
    store = artifacts or InMemoryArtifactStore()
    events = RecordingEventSink()
    replayer = Replayer(
        launcher=launcher,
        artifacts=store,
        events=events,
        secrets=DictSecretResolver({"portal_password": SECRET} if secrets is None else secrets),
        clock=FakeClock(CREATED_AT_DATETIME),
        timer=page.timer,
        randomness=SequenceRandom([0.0]),
        run_ids=SequentialRunIds(),
        config=config(**overrides),
    )
    return Harness(flow, page, launcher, store, events, replayer)


async def test_a_successful_run_emits_every_event_in_order() -> None:
    subject = harness()

    run = await subject.run()

    step = [
        "step_started",
        "target_resolved",
        "action_performed",
        "checkpoint_passed",
        "step_succeeded",
    ]
    assert subject.events.types == [
        "run_started",
        "step_started", "action_performed", "checkpoint_passed", "step_succeeded",
        *step, *step,
        *step[:4], "checkpoint_passed", "step_succeeded",
        "run_finished",
    ]  # fmt: skip
    assert [event.sequence for event in subject.events.events] == list(
        range(1, len(subject.events.events) + 1)
    )
    assert {event.run_id for event in subject.events.events} == {run.run_id}
    assert run.status is RunStatus.SUCCEEDED


async def test_the_run_record_is_stored_with_every_step_and_its_evidence() -> None:
    subject = harness()

    run = await subject.run()

    assert subject.stored(run) == run
    assert [step.status for step in run.steps] == [StepStatus.SUCCEEDED] * 4
    assert run.inputs == INPUTS
    assert run.steps[3].artifacts.download == "downloads/report.csv"
    target = run.steps[3].target
    assert target is not None
    assert target.resolved_rank == 0
    assert subject.artifacts.names(run.run_id) == [
        "downloads/report.csv",
        "run.json",
        "steps/001_open.png",
        "steps/002_fill_email.png",
        "steps/003_fill_password.png",
        "steps/004_export.png",
    ]
    assert run.steps[3].checkpoints[0].kind == "download_completed"


async def test_event_checkpoints_are_watched_before_the_action_and_released_after() -> None:
    subject = harness()

    await subject.run()

    calls = subject.page.calls
    assert calls.index("watch:download") < calls.index("click:export") < calls.index("unwatch")


async def test_a_failed_checkpoint_still_releases_its_watch() -> None:
    flow = workflow()
    subject = harness(flow=flow, page=page_for(flow, on_action=lambda page: None))

    run = await subject.run()

    error = run.steps[3].error
    assert error is not None
    assert error.type == "CheckpointFailed"
    assert subject.page.calls[-4:] == ["unwatch", "screenshot", "dom_snapshot", "export_trace"]


async def test_a_failed_step_stops_the_run_with_its_evidence() -> None:
    flow = workflow()
    page = page_for(flow)
    page.navigations = [NavigationOutcome(url="https://elsewhere.test/", status=200)]
    subject = harness(flow=flow, page=page)

    run = await subject.run()

    assert [step.status for step in run.steps] == [StepStatus.FAILED, *[StepStatus.NOT_RUN] * 3]
    assert run.error is not None
    assert (run.error.type, run.error.category) == ("CheckpointFailed", ErrorCategory.STEP)
    assert subject.events.types.count("step_started") == 1
    assert run.steps[0].artifacts.dom_snapshot == "failure/001_open.dom.html"
    assert run.steps[0].action_performed


async def test_a_target_nothing_matches_abstains_without_retrying_or_acting() -> None:
    flow = workflow()
    page = page_for(flow)
    page.finds.clear()
    subject = harness(flow=flow, page=page)

    run = await subject.run()

    error = run.steps[1].error
    assert error is not None
    assert (error.type, error.context["reason"]) == ("HealAbstained", "no_candidates")
    assert subject.events.types.count("step_started") == 2
    assert not run.steps[1].action_performed
    assert "fill:email" not in page.calls


async def test_secret_values_never_reach_events_records_or_snapshots() -> None:
    flow = workflow()
    refused = TargetNotActionable(
        f"the browser refused while holding {SECRET}", detail=f"typed {SECRET}"
    )
    page = page_for(flow, action_error=refused)
    page.html = f"<input value='{SECRET}'>"
    subject = harness(flow=flow, page=page)

    run = await subject.run()

    everything = [event.model_dump_json() for event in subject.events.events]
    everything += [
        data.decode("utf-8", errors="replace") for data in subject.artifacts.files.values()
    ]
    assert all(SECRET not in text for text in everything)
    assert run.error is not None
    assert "[REDACTED]" in run.error.message
    assert [value for value in page.typed if isinstance(value, SecretText)]


async def test_fields_filled_from_secrets_are_masked_in_every_later_screenshot() -> None:
    subject = harness()

    await subject.run()

    password = subject.flow.steps[2].target.selectors[0]  # type: ignore[union-attr]
    assert [password in masks for masks in subject.page.masks] == [False, False, True, True]


async def test_a_withheld_trace_names_the_step_that_typed_the_value() -> None:
    flow = workflow()
    page = page_for(flow, on_action=lambda page: None)
    page.trace = TraceNotSaved(reason=TraceWithheldReason.SECRET_BEARING_PAGE)
    subject = harness(flow=flow, page=page)

    run = await subject.run()

    withheld = run.steps[3].artifacts.trace_withheld
    assert withheld is not None
    assert (withheld.typed_at_index, withheld.typed_at_step) == (2, "fill_password")


async def test_a_saved_trace_is_kept_with_the_failure() -> None:
    flow = workflow()
    page = page_for(flow, on_action=lambda page: None)
    page.trace = TraceSaved(path=Path("/browser/work/trace.zip"))
    subject = harness(flow=flow, page=page)

    run = await subject.run()

    assert run.steps[3].artifacts.trace == "failure/trace.zip"


async def test_an_action_that_opens_a_new_page_fails_the_step() -> None:
    flow = workflow()
    subject = harness(
        flow=flow, page=page_for(flow, on_action=lambda page: setattr(page, "opened_pages", 1))
    )

    run = await subject.run()

    assert run.steps[3].error is not None
    assert (run.steps[3].error.type, run.steps[3].error.context["reason"]) == (
        "NavigationError",
        "new_page_opened",
    )


async def test_a_transient_navigation_failure_is_retried() -> None:
    flow = workflow()
    page = page_for(flow)
    page.navigations = [
        NavigationError("refused", reason="ERR_CONNECTION_RESET"),
        NavigationOutcome(url=PORTAL, status=200),
    ]
    subject = harness(flow=flow, page=page)

    run = await subject.run()

    navigation = run.steps[0].navigation
    assert navigation is not None
    assert navigation.attempts == 2
    assert page.timer.pauses == [0.5]


async def test_invalid_inputs_are_refused_before_anything_starts() -> None:
    subject = harness()

    with pytest.raises(RunInputError):
        await subject.run({"portal_url": PORTAL})

    assert (subject.launcher.sessions, subject.artifacts.files, subject.events.events) == (
        [],
        {},
        [],
    )


async def test_a_missing_secret_is_refused_before_anything_starts() -> None:
    subject = harness(secrets={})

    with pytest.raises(SecretUnavailable) as caught:
        await subject.run()

    assert caught.value.context["names"] == ["portal_password"]
    assert subject.launcher.sessions == []


async def test_a_browser_that_cannot_start_fails_the_run_as_infrastructure() -> None:
    subject = harness()
    subject.launcher.error = BrowserUnavailable("chromium is not installed")

    run = await subject.run()

    assert run.status is RunStatus.FAILED
    assert run.error is not None
    assert (run.error.type, run.error.category) == (
        "BrowserUnavailable",
        ErrorCategory.INFRASTRUCTURE,
    )
    assert subject.stored(run) == run
    assert subject.events.types[-1] == "run_finished"


async def test_an_unexpected_error_is_recorded_then_raised_as_infrastructure() -> None:
    flow = workflow()

    def explode(page: FakeBrowser) -> None:
        raise RuntimeError("adapter bug")

    subject = harness(flow=flow, page=page_for(flow, on_action=explode))

    with pytest.raises(InfrastructureError) as caught:
        await subject.run()

    assert isinstance(caught.value.__cause__, RuntimeError)
    stored = json.loads(
        next(data for (_, name), data in subject.artifacts.files.items() if name == "run.json")
    )
    assert (stored["status"], stored["error"]["type"]) == ("failed", "RuntimeError")


async def test_a_step_cut_short_by_the_run_limit_fails_as_a_run_timeout() -> None:
    flow = workflow()
    page = page_for(flow)
    page.finds.clear()
    subject = harness(flow=flow, page=page, run_timeout_ms=1_000, step_timeout_ms=5_000)

    run = await subject.run()

    error = run.steps[1].error
    assert error is not None
    assert (error.type, error.context["interrupted_error"]) == ("RunTimedOut", "TargetNotFound")


async def test_a_run_out_of_time_between_steps_stops_before_the_next_one() -> None:
    flow = workflow()
    page = page_for(flow)
    page.elements["email"].on_action = lambda fake: fake.timer.advance_ms(5_000)
    subject = harness(flow=flow, page=page, run_timeout_ms=2_000)

    run = await subject.run()

    assert [step.status for step in run.steps] == [
        StepStatus.SUCCEEDED,
        StepStatus.SUCCEEDED,
        StepStatus.NOT_RUN,
        StepStatus.NOT_RUN,
    ]
    assert run.error is not None
    assert run.error.type == "RunTimedOut"


async def test_a_store_that_cannot_write_stops_the_run_before_it_starts() -> None:
    subject = harness(artifacts=InMemoryArtifactStore(fail=True))

    with pytest.raises(ArtifactStoreUnavailable):
        await subject.run()

    assert subject.launcher.sessions == []


async def test_fill_events_say_what_kind_of_value_was_typed_but_never_the_value() -> None:
    subject = harness()

    await subject.run()

    kinds = [
        event.value_kind for event in subject.events.events if event.type == "action_performed"
    ]
    assert kinds == [ValueKind.INPUT, ValueKind.INPUT, ValueKind.SECRET, None]
