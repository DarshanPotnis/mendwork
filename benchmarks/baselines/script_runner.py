"""Plain Playwright scripts: the benchmark's first two baselines (ADR 0014).

A hand-written script finds each control with one locator and acts through Playwright's own
auto-waiting and strict mode. Nothing checks the element's identity, compares candidates, or heals:
if the locator finds one element, the script acts on it, whatever it now says. That is exactly what
the baselines measure, so none of it may be routed through Mendwork's replayer, whose Rung 0 would
refuse a control that kept its id but changed its name.

The one thing the scripts share with Mendwork is verification: after each action they check the
workflow's own checkpoints through the engine's checkpoint evaluation, so the comparison isolates
how a target is found. A plain script usually has fewer assertions and would carry on after a wrong
click, so the wrong actions counted here are a lower bound.
"""

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_hex
from typing import Final, Protocol

from playwright.async_api import ElementHandle, Locator, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from benchmarks.chaos.ground_truth import (
    SIGNED_IN_SCRIPT,
    MatchedKeys,
    TargetAvailable,
    TargetKnown,
    WrongActionCount,
    ground_truth_known,
    matched_keys,
    target_available,
    wrong_action_count,
)
from benchmarks.chaos.systems import ScriptKind
from mendwork.adapters.browser_playwright.locators import build_locator
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.adapters.system.timer import AsyncioTimer
from mendwork.engine.benchmark.cells import UNRECORDED, RunFailure
from mendwork.engine.benchmark.truth import (
    ActionRecord,
    Resolution,
    StepObservation,
    StopKind,
    Verdict,
    target_match,
)
from mendwork.engine.domain.enums import SelectorStrategy
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.run_identifiers import RunId, parse_run_id
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.steps import (
    ClickStep,
    FillStep,
    NavigateStep,
    PressStep,
    SelectStep,
    Step,
)
from mendwork.engine.domain.values import InputValue, LiteralValue, SecretValue
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import MendworkError
from mendwork.engine.ports.browser_types import (
    ElementRef,
    EqualsText,
    FieldExpectation,
    NonEmpty,
    WatchId,
)
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.heal_policy import verification_strength
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.engine.verification.checkpoints import (
    CheckpointContext,
    evaluate_checkpoint,
    evaluation_order,
    watch_kinds,
)
from mendwork.settings import Settings

STRICT_MODE: Final = "strict mode violation"
SUCCEEDED: Final = "succeeded"
FAILED: Final = "failed"


@dataclass(frozen=True, slots=True)
class ScriptRun:
    """One script's run of a workflow: an observation per targeted step, and its status."""

    status: str
    observations: tuple[StepObservation, ...]
    duration_ms: int
    failure: RunFailure | None = None
    """Where the run stopped, including at a navigate step, which has no ground-truth target."""


@dataclass(frozen=True, slots=True)
class _StepEnd:
    stop: StopKind
    reason: str | None = None
    actions: tuple[ActionRecord, ...] = ()
    page_wrong: int = 0


def script_selector(target: Fingerprint, kind: ScriptKind) -> Selector | None:
    """The one selector a script of this kind would have written for a target, if it has one."""
    if kind == "css_selector":
        wanted: tuple[SelectorStrategy, ...] = (SelectorStrategy.CSS,)
    else:
        # Password and date inputs have no ARIA role; Playwright's docs locate fields by label.
        wanted = (SelectorStrategy.ROLE_NAME, SelectorStrategy.LABEL)
    for strategy in wanted:
        found = next(
            (selector for selector in target.selectors if selector.strategy is strategy), None
        )
        if found is not None:
            return found
    return None


def new_run_id() -> str:
    """A run id in Mendwork's format, for the session's working directory name."""
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{token_hex(4)}"


class ScriptSessions(Protocol):
    """Opens one browser session for a script's run: the product's launcher, or a test's."""

    def session(
        self, run_id: RunId, egress: EgressPolicy
    ) -> AbstractAsyncContextManager[PlaywrightSession]:
        """A fresh context and page, held to ``egress``, closed when the run ends."""
        ...


class ScriptRunner:
    """Runs a workflow the way a plain Playwright script would, checking every action first."""

    def __init__(
        self,
        launcher: ScriptSessions,
        settings: Settings,
        *,
        kind: ScriptKind,
        targets: Mapping[str, str],
        inputs: Mapping[str, str],
        secrets: Mapping[str, str],
        egress: EgressPolicy,
        signed_in: bool,
        matched: MatchedKeys = matched_keys,
        available: TargetAvailable = target_available,
        wrong_count: WrongActionCount = wrong_action_count,
        known: TargetKnown = ground_truth_known,
    ) -> None:
        self._launcher = launcher
        self._settings = settings
        self._kind = kind
        self._targets = targets
        self._keys = sorted(set(targets.values()))
        self._inputs = inputs
        self._secrets = secrets
        self._egress = egress
        self._signed_in = signed_in
        self._matched = matched
        self._available = available
        self._wrong_count = wrong_count
        self._known = known
        self._timer = AsyncioTimer()

    async def run(self, workflow: WorkflowVersion) -> ScriptRun:
        """Every step in order, stopping at the first that does not complete."""
        started = self._timer.monotonic()
        deadline = Deadline.after(self._timer, self._settings.run_timeout_ms)
        observations: list[StepObservation] = []
        stopped = False
        failure: RunFailure | None = None
        async with self._launcher.session(parse_run_id(new_run_id()), self._egress) as session:
            if self._signed_in:
                await session.page.add_init_script(script=SIGNED_IN_SCRIPT)
            for step in workflow.steps:
                key = self._targets.get(step.id)
                strength = verification_strength(step.checkpoints)
                if stopped:
                    if key is not None:
                        observations.append(
                            StepObservation(
                                step_id=step.id, stop=StopKind.NOT_REACHED, strength=strength
                            )
                        )
                    continue
                step_started = self._timer.monotonic()
                end = await self._step(session, step, key, deadline)
                duration = round((self._timer.monotonic() - step_started) * 1000)
                stopped = end.stop is not StopKind.COMPLETED
                if stopped and failure is None:
                    # Recorded whether or not the step has a target, so a navigate that could not
                    # load the page still says why the run ended.
                    failure = RunFailure(
                        step_id=step.id,
                        action=step.action,
                        stop=end.stop,
                        reason=end.reason or UNRECORDED,
                        targeted=key is not None,
                    )
                if key is None:
                    continue
                available = (
                    await self._available(session.page, key)
                    if end.stop is StopKind.DECLINED
                    else None
                )
                observations.append(
                    StepObservation(
                        step_id=step.id,
                        stop=end.stop,
                        stop_reason=end.reason,
                        actions=end.actions,
                        page_wrong_actions=end.page_wrong,
                        target_available_at_stop=available,
                        strength=strength,
                        duration_ms=duration,
                    )
                )
        return ScriptRun(
            status=FAILED if stopped else SUCCEEDED,
            observations=tuple(observations),
            duration_ms=round((self._timer.monotonic() - started) * 1000),
            failure=failure,
        )

    async def _step(
        self, session: PlaywrightSession, step: Step, key: str | None, deadline: Deadline
    ) -> _StepEnd:
        page = session.page
        if isinstance(step, NavigateStep):
            try:
                await page.goto(
                    self._plain(step.value), timeout=self._settings.navigation_timeout_ms
                )
            except PlaywrightError as error:
                return _StepEnd(StopKind.ERROR, _first_line(error))
            verdict = await self._verify(session, step, None, None, None, deadline)
            if verdict:
                return _StepEnd(StopKind.COMPLETED)
            return _StepEnd(StopKind.CHECKPOINT_FAILED, "checkpoint_failed")
        target = _target_of(step)
        locator: Locator | None = None
        handle: ElementHandle | None = None
        found: tuple[str, ...] = ()
        known = True
        if target is not None:
            selector = script_selector(target, self._kind)
            if selector is None:
                return _StepEnd(StopKind.UNEXPRESSIBLE, f"no {self._kind} locator")
            locator = build_locator(page, selector)
            try:
                handle = await locator.element_handle(timeout=self._settings.step_timeout_ms)
            except PlaywrightTimeoutError:
                return _StepEnd(StopKind.DECLINED, "locator_timeout")
            except PlaywrightError as error:
                return _refused(error)
            found = await self._matched(page, self._keys, handle)
            known = True if key is None else await self._known(page, key)
        kinds = watch_kinds(step.checkpoints)
        watch = await session.watch(kinds) if kinds else None
        try:
            before = await self._wrong_count(page)
            try:
                expectation = await self._act(page, step, locator)
            except PlaywrightTimeoutError:
                return _StepEnd(StopKind.DECLINED, "locator_timeout")
            except PlaywrightError as error:
                return _refused(error)
            after = await self._wrong_count(page)
            page_wrong = after - before if before is not None and after is not None else 0
            pinned = session.pin_handle(handle) if handle is not None else None
            passed = await self._verify(session, step, watch, pinned, expectation, deadline)
        except MendworkError as error:
            return _StepEnd(StopKind.ERROR, type(error).__name__)
        finally:
            if watch is not None:
                await session.unwatch(watch)
        actions: tuple[ActionRecord, ...] = ()
        if handle is not None:
            actions = (
                ActionRecord(
                    action=step.action,
                    resolution=Resolution.DIRECT,
                    on_target=target_match(key is not None and key in found, known),
                    verdict=Verdict.PASSED if passed else Verdict.FAILED,
                ),
            )
        return _StepEnd(
            StopKind.COMPLETED if passed else StopKind.CHECKPOINT_FAILED,
            None if passed else "checkpoint_failed",
            actions,
            max(0, page_wrong),
        )

    async def _act(
        self, page: Page, step: Step, locator: Locator | None
    ) -> FieldExpectation | None:
        timeout = self._settings.step_timeout_ms
        match step:
            case ClickStep():
                await _require(locator).click(timeout=timeout)
            case FillStep():
                value = step.value
                if isinstance(value, SecretValue):
                    await _require(locator).fill(self._secrets[value.name], timeout=timeout)
                    return NonEmpty()
                text = self._plain(value)
                await _require(locator).fill(text, timeout=timeout)
                return EqualsText(value=text)
            case SelectStep():
                await _require(locator).select_option(
                    label=self._plain(step.value), timeout=timeout
                )
            case PressStep():
                if locator is None:
                    await page.keyboard.press(step.key)
                else:
                    await locator.press(step.key, timeout=timeout)
            case NavigateStep():
                raise ValueError("navigate steps are not acted on through a locator")
        return None

    async def _verify(
        self,
        session: PlaywrightSession,
        step: Step,
        watch: WatchId | None,
        pinned: ElementRef | None,
        expectation: FieldExpectation | None,
        deadline: Deadline,
    ) -> bool:
        await session.wait_until_settled(
            quiet_frames=self._settings.settle_quiet_frames,
            timeout_ms=deadline.cap(self._settings.settle_timeout_ms),
        )
        context = CheckpointContext(
            browser=session,
            timer=self._timer,
            run_deadline=deadline,
            default_timeout_ms=self._settings.checkpoint_timeout_ms,
            scrubber=SecretScrubber(),
            watch=watch,
            target=pinned,
            expectation=expectation,
        )
        for position, checkpoint in evaluation_order(step.checkpoints):
            outcome = await evaluate_checkpoint(context, position, checkpoint)
            if not outcome.result.passed:
                return False
        return True

    def _plain(self, value: LiteralValue | InputValue) -> str:
        match value:
            case LiteralValue():
                return value.value
            case InputValue():
                return self._inputs[value.name]


def _target_of(step: Step) -> Fingerprint | None:
    match step:
        case ClickStep() | FillStep() | SelectStep():
            return step.target
        case PressStep():
            return step.target
        case NavigateStep():
            return None


def _require(locator: Locator | None) -> Locator:
    if locator is None:
        raise ValueError("this step acts on a target, and has no locator")
    return locator


def _refused(error: PlaywrightError) -> _StepEnd:
    if STRICT_MODE in error.message:
        return _StepEnd(StopKind.DECLINED, "strict_mode_violation")
    return _StepEnd(StopKind.ERROR, _first_line(error))


def _first_line(error: PlaywrightError) -> str:
    line = error.message.splitlines()[0] if error.message else type(error).__name__
    return line[:200]
