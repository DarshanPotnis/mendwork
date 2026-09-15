"""The egress policy in the engine: before every navigation, before a run starts, and in a step."""

from typing import Final

import pytest
import structlog

from mendwork.engine.domain.runs import RunStatus, StepStatus
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import EgressBlocked, NavigationError
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.navigation import navigate_with_retry
from mendwork.engine.replay.navigation_guard import NavigationGuard
from mendwork.engine.replay.replayer import Replayer
from mendwork.engine.safety.egress import (
    EgressPolicy,
    EgressRefusal,
    EgressRule,
    LoopbackException,
)
from mendwork.engine.safety.egress_addresses import AddressRange
from mendwork.engine.safety.egress_blocks import EgressBlock, EgressLayer, egress_blocked
from mendwork.engine.safety.secret_scrub import SecretScrubber
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
from tests.unit.replay.builders import browser, button, config, selector
from tests.workflows import CREATED_AT_DATETIME, fingerprint, input_ref, literal, version

pytestmark = pytest.mark.asyncio

PORTAL: Final = "https://portal.example.test/sign-in"
UNLISTED: Final = "https://intranet.example.com/"
NOT_RESOLVED: Final = "ERR_NAME_RESOLUTION_FAILED"
EXPORT_ID: Final = selector(strategy="test_id", value="export")


def open_step(value: object, step_id: str = "open") -> dict[str, object]:
    return {
        "id": step_id,
        "intent": "Open the portal",
        "action": "navigate",
        "risk": "safe",
        "value": value,
    }


def export_step() -> dict[str, object]:
    return {
        "id": "export",
        "intent": "Click the 'Export' button",
        "action": "click",
        "risk": "safe",
        "target": fingerprint(accessible_name="Export", selectors=[EXPORT_ID.model_dump()]),
        "checkpoints": [{"kind": "text_present", "text": "Exported"}],
    }


def private_connection() -> EgressBlock:
    return EgressBlock(
        layer=EgressLayer.CONNECTION,
        refusal=EgressRefusal(
            rule=EgressRule.BLOCKED_ADDRESS,
            host="intranet.example.test",
            port=80,
            address="10.0.0.5",
            address_range=AddressRange.PRIVATE,
            detail="intranet.example.test resolves to 10.0.0.5, which is a private address",
        ),
    )


async def navigate(page: FakeBrowser, url: str, guard: NavigationGuard) -> object:
    return await navigate_with_retry(
        page,
        url,
        guard=guard,
        policy=config().retry,
        navigation_timeout_ms=3_000,
        deadline=Deadline.after(page.timer, 60_000),
        timer=page.timer,
        randomness=SequenceRandom([0.0]),
        scrubber=SecretScrubber(),
        log=structlog.stdlib.get_logger("tests.egress"),
    )


def replayer(page: FakeBrowser, launcher: FakeLauncher, events: RecordingEventSink) -> Replayer:
    return Replayer(
        launcher=launcher,
        artifacts=InMemoryArtifactStore(),
        events=events,
        secrets=DictSecretResolver({}),
        clock=FakeClock(CREATED_AT_DATETIME),
        timer=page.timer,
        randomness=SequenceRandom([0.0]),
        run_ids=SequentialRunIds(),
        config=config(),
        egress=TEST_POLICY,
        resolver=FakeResolver(),
        records=InMemoryRunRecords(),
    )


async def test_an_allowlisted_name_with_public_addresses_passes_after_one_lookup() -> None:
    resolver = FakeResolver()

    await NavigationGuard(policy=TEST_POLICY, resolver=resolver).check(PORTAL, timeout_ms=1_000)

    assert resolver.lookups == ["portal.example.test"]


async def test_a_host_off_the_allowlist_is_refused_without_being_looked_up() -> None:
    resolver = FakeResolver()
    guard = NavigationGuard(policy=TEST_POLICY, resolver=resolver)

    with pytest.raises(EgressBlocked) as caught:
        await guard.check(f"{UNLISTED}reports?token=abc", timeout_ms=1_000)

    context = caught.value.context
    assert (context["rule"], context["host"], context["layer"]) == (
        "not_allowlisted",
        "intranet.example.com",
        "navigation",
    )
    assert "token" not in caught.value.message
    assert resolver.lookups == []


async def test_an_allowlisted_name_that_resolves_to_a_private_address_is_refused() -> None:
    resolver = FakeResolver({"portal.example.test": ("93.184.215.14", "10.0.0.7")})

    with pytest.raises(EgressBlocked) as caught:
        await NavigationGuard(policy=TEST_POLICY, resolver=resolver).check(PORTAL, timeout_ms=1)

    context = caught.value.context
    assert (context["rule"], context["address"], context["address_range"]) == (
        "blocked_address",
        "10.0.0.7",
        "private",
    )


async def test_a_lookup_that_fails_is_a_navigation_error_not_a_refusal() -> None:
    failed = NavigationError("no such host", reason=NOT_RESOLVED)
    guard = NavigationGuard(policy=TEST_POLICY, resolver=FakeResolver(failure=failed))

    with pytest.raises(NavigationError) as caught:
        await guard.check(PORTAL, timeout_ms=1_000)

    assert caught.value.context["reason"] == NOT_RESOLVED


async def test_an_answer_that_is_not_an_address_counts_as_a_failed_lookup() -> None:
    resolver = FakeResolver({"portal.example.test": ("portal.internal",)})

    with pytest.raises(NavigationError) as caught:
        await NavigationGuard(policy=TEST_POLICY, resolver=resolver).check(PORTAL, timeout_ms=1)

    assert caught.value.context["reason"] == NOT_RESOLVED


async def test_a_literal_loopback_origin_needs_its_exact_exception_and_no_lookup() -> None:
    policy = EgressPolicy(loopback_exceptions=(LoopbackException(address="127.0.0.1", port=8765),))
    resolver = FakeResolver()
    guard = NavigationGuard(policy=policy, resolver=resolver)

    await guard.check("http://127.0.0.1:8765/index.html", timeout_ms=1_000)
    with pytest.raises(EgressBlocked):
        await guard.check("http://127.0.0.1:8766/index.html", timeout_ms=1_000)

    assert resolver.lookups == []


async def test_a_refused_navigation_is_never_retried_and_never_reaches_the_browser() -> None:
    page = browser()

    with pytest.raises(EgressBlocked):
        await navigate(page, UNLISTED, NavigationGuard(policy=TEST_POLICY, resolver=FakeResolver()))

    assert page.calls_named("navigate") == []


async def test_a_failed_lookup_is_retried_like_any_transient_navigation_failure() -> None:
    page = browser()
    failed = NavigationError("no such host", reason=NOT_RESOLVED)
    resolver = FakeResolver(failure=failed)

    with pytest.raises(NavigationError) as caught:
        await navigate(page, PORTAL, NavigationGuard(policy=TEST_POLICY, resolver=resolver))

    assert caught.value.context["attempts"] == 3
    assert resolver.lookups == ["portal.example.test"] * 3
    assert page.calls_named("navigate") == []


async def test_preflight_refuses_a_literal_or_an_input_url_the_policy_refuses() -> None:
    guard = NavigationGuard(policy=TEST_POLICY, resolver=FakeResolver())
    literal_flow = version(steps=[open_step(literal(PORTAL)), open_step(literal(UNLISTED), "next")])
    input_flow = version(
        inputs=[{"name": "portal_url", "kind": "url"}], steps=[open_step(input_ref("portal_url"))]
    )

    with pytest.raises(EgressBlocked):
        await guard.preflight(literal_flow, {}, timeout_ms=1_000)
    with pytest.raises(EgressBlocked):
        await guard.preflight(input_flow, {"portal_url": UNLISTED}, timeout_ms=1_000)  # type: ignore[dict-item]  # InputName is a str NewType
    await guard.preflight(input_flow, {"portal_url": PORTAL}, timeout_ms=1_000)  # type: ignore[dict-item]  # as above


async def test_preflight_lets_a_name_that_does_not_resolve_yet_through() -> None:
    failed = NavigationError("no such host", reason=NOT_RESOLVED)
    guard = NavigationGuard(policy=TEST_POLICY, resolver=FakeResolver(failure=failed))

    await guard.preflight(version(steps=[open_step(literal(PORTAL))]), {}, timeout_ms=1_000)


async def test_a_refused_start_url_fails_preflight_and_creates_nothing() -> None:
    page = browser()
    launcher = FakeLauncher(page)
    events = RecordingEventSink()
    flow: WorkflowVersion = version(steps=[open_step(literal(UNLISTED))])

    with pytest.raises(EgressBlocked):
        await replayer(page, launcher, events).run(flow, {})

    assert (launcher.sessions, events.events) == ([], [])


async def test_a_refusal_during_a_step_fails_it_before_its_checkpoints_and_is_never_healed() -> (
    None
):
    page = browser(
        elements={
            "export": button(
                "Export", on_action=lambda p: p.egress_blocks.append(private_connection())
            )
        },
        finds={EXPORT_ID: "export"},
    )
    launcher = FakeLauncher(page)
    events = RecordingEventSink()
    flow = version(steps=[open_step(literal(PORTAL)), export_step()])

    run = await replayer(page, launcher, events).run(flow, {})

    stopped = run.steps[1]
    assert run.status is RunStatus.FAILED
    assert stopped.status is StepStatus.FAILED
    assert stopped.error is not None
    assert (stopped.error.type, stopped.error.context["address"]) == ("EgressBlocked", "10.0.0.5")
    assert (stopped.action_performed, stopped.checkpoints, stopped.heal) == (True, (), None)
    assert launcher.policies == [TEST_POLICY]


async def test_an_egress_failure_names_its_first_refusal_and_keeps_every_block() -> None:
    first, second = private_connection(), private_connection()

    error = egress_blocked([first, second])

    assert error.message == (
        "the egress policy refused intranet.example.test: intranet.example.test resolves to "
        "10.0.0.5, which is a private address"
    )
    assert len(error.context["blocks"]) == 2  # type: ignore[arg-type]  # a list of blocks
    with pytest.raises(ValueError, match="at least one"):
        egress_blocked([])
