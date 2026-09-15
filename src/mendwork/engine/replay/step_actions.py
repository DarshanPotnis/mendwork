"""Performing a step on its target: check, watch, act, settle, verify.

The same sequence runs for a target Rung 0 verified, a healed target, and a step replayed
while a page is restored, so a heal is held to exactly the checks and checkpoints an
unhealed step is. A quiet step (a replay) emits no events and keeps no download. After the
action and after the checkpoints, a new tab or window fails the step, and so does anything the
run's egress policy refused while the step ran (ADR 0011).

An irreversible step's dispatch is journaled before the action is sent, so a run interrupted from
that moment on needs review; while any action is being sent, the step's progress says so.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.steps import (
    ClickStep,
    FillStep,
    NavigateStep,
    PressStep,
    SelectStep,
    Step,
)
from mendwork.engine.errors import CheckpointFailed, MendworkError, NavigationError
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import ElementIdentity, ElementRef, SecretText, WatchId
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.evidence import EvidenceRecorder
from mendwork.engine.replay.navigation import navigate_with_retry
from mendwork.engine.replay.navigation_guard import NavigationGuard
from mendwork.engine.replay.progress import StepProgress
from mendwork.engine.replay.values import TypedValue, ValueResolver
from mendwork.engine.safety.egress_blocks import egress_blocked
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.engine.verification.checkpoints import (
    CheckpointContext,
    evaluate_checkpoint,
    evaluation_order,
    watch_kinds,
)
from mendwork.engine.verification.preaction import ensure_actionable

IrreversibleDispatch = Callable[[int, Step], Awaitable[None]]
"""Called, and awaited, just before an irreversible step's action is sent."""


@dataclass(frozen=True, slots=True)
class ActionTarget:
    """The element a step acts on, verified by Rung 0 or accepted by a heal."""

    element: ElementRef
    identity: ElementIdentity
    mask: Selector | None
    """Finds the field in screenshots, should the step type a secret into it."""


class StepActions:
    """Acts on one run's page and verifies the result."""

    def __init__(
        self,
        *,
        browser: BrowserPort,
        emitter: RunEmitter,
        guard: NavigationGuard,
        on_irreversible: IrreversibleDispatch,
        values: ValueResolver,
        evidence: EvidenceRecorder,
        scrubber: SecretScrubber,
        timer: Timer,
        randomness: RandomSource,
        config: ReplayConfig,
        run_deadline: Deadline,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        self._browser = browser
        self._emitter = emitter
        self._guard = guard
        self._on_irreversible = on_irreversible
        self._values = values
        self._evidence = evidence
        self._scrubber = scrubber
        self._timer = timer
        self._randomness = randomness
        self._config = config
        self._run_deadline = run_deadline
        self._log = log

    async def perform(
        self, progress: StepProgress, target: ActionTarget | None, deadline: Deadline
    ) -> None:
        """Check the target can take the action, act, and verify every checkpoint.

        Raises CheckpointFailed when a checkpoint does not pass, the pre-action errors
        (TargetNotFound, TargetDrifted, TargetNotActionable) before anything is performed, and
        EgressBlocked when the egress policy refused anything the step led the browser to.
        """
        step = progress.step
        if target is not None:
            await ensure_actionable(
                self._browser,
                target.element,
                target.identity,
                step.action,
                deadline,
                self._scrubber,
            )
        kinds = watch_kinds(step.checkpoints)
        watch = await self._browser.watch(kinds) if kinds else None
        try:
            typed = await self._act(progress, target, deadline)
            # Let what the action caused arrive (a load, a banner, a new tab) before anything
            # is checked: a bounded wait for a quiet DOM, never a pause for time.
            await self._browser.wait_until_settled(
                quiet_frames=self._config.settle_quiet_frames,
                timeout_ms=deadline.cap(self._config.settle_timeout_ms),
            )
            await self._fail_on_page_problems()
            await self._verify(progress, watch, target, typed)
        finally:
            if watch is not None:
                await self._browser.unwatch(watch)

    async def _act(
        self, progress: StepProgress, target: ActionTarget | None, deadline: Deadline
    ) -> TypedValue | None:
        step = progress.step
        if step.risk is RiskLevel.IRREVERSIBLE and not progress.quiet:
            await self._on_irreversible(progress.index, step)
        # An interrupt leaves the flag set: it arrived while the action was on its way.
        progress.dispatching = True
        try:
            typed = await self._dispatch(progress, target, deadline)
        except MendworkError:
            progress.dispatching = False
            raise
        progress.dispatching = False
        return typed

    async def _dispatch(
        self, progress: StepProgress, target: ActionTarget | None, deadline: Deadline
    ) -> TypedValue | None:
        step = progress.step
        typed: TypedValue | None = None
        match step:
            case NavigateStep():
                report = await navigate_with_retry(
                    self._browser,
                    self._values.plain(step.value),
                    guard=self._guard,
                    policy=self._config.retry,
                    navigation_timeout_ms=self._config.navigation_timeout_ms,
                    deadline=self._run_deadline,
                    timer=self._timer,
                    randomness=self._randomness,
                    scrubber=self._scrubber,
                    log=self._log.bind(step_id=step.id),
                )
                progress.navigation = report
                progress.action_performed = True
                if not progress.quiet:
                    await self._emitter.action_performed(
                        progress.index, step, value_kind=step.value.kind, navigation=report
                    )
                return None
            case ClickStep():
                await self._browser.click(_element(target), timeout_ms=deadline.timeout_ms())
            case FillStep():
                pinned = _require(target)
                typed = await self._values.typed(step.value)
                if isinstance(typed.text, SecretText):
                    self._evidence.secret_typed(progress.index, step.id, pinned.mask)
                await self._browser.fill(
                    pinned.element, typed.text, timeout_ms=deadline.timeout_ms()
                )
            case SelectStep():
                await self._browser.select_option(
                    _element(target),
                    self._values.plain(step.value),
                    timeout_ms=deadline.timeout_ms(),
                )
            case PressStep():
                element = target.element if target is not None else None
                await self._browser.press(element, step.key, timeout_ms=deadline.timeout_ms())
        progress.action_performed = True
        if not progress.quiet:
            await self._emitter.action_performed(
                progress.index, step, value_kind=typed.kind if typed is not None else None
            )
        return typed

    async def _verify(
        self,
        progress: StepProgress,
        watch: WatchId | None,
        target: ActionTarget | None,
        typed: TypedValue | None,
    ) -> None:
        step = progress.step
        context = CheckpointContext(
            browser=self._browser,
            timer=self._timer,
            run_deadline=self._run_deadline,
            default_timeout_ms=self._config.checkpoint_timeout_ms,
            scrubber=self._scrubber,
            watch=watch,
            target=target.element if target is not None else None,
            expectation=typed.expectation if typed is not None else None,
        )
        for position, checkpoint in evaluation_order(step.checkpoints):
            outcome = await evaluate_checkpoint(context, position, checkpoint)
            if outcome.download is not None and not progress.quiet:
                progress.download = await self._evidence.keep_download(
                    outcome.download, progress.index, step.id
                )
            result = outcome.result
            progress.checkpoints.append(result)
            if not progress.quiet:
                await self._emitter.checkpoint(progress.index, step.id, result)
            if not result.passed:
                # A refused navigation makes checkpoints fail; the refusal is the real cause.
                await self._fail_on_page_problems()
                raise CheckpointFailed(
                    f"checkpoint {position + 1} ({result.kind}) did not pass: {result.reason}",
                    checkpoint_index=position,
                    kind=result.kind.value,
                    reason=result.reason,
                    detail=result.detail,
                )
        await self._fail_on_page_problems()

    async def _fail_on_page_problems(self) -> None:
        opened = await self._browser.take_opened_pages()
        if opened:
            raise NavigationError(
                "the action opened a new tab or window; workflows that span several pages at "
                "once are not supported",
                reason="new_page_opened",
                pages=opened,
            )
        blocks = await self._browser.take_egress_blocks()
        if blocks:
            raise egress_blocked(blocks)


def _require(target: ActionTarget | None) -> ActionTarget:
    if target is None:
        raise MendworkError("a step with a target reached its action without a verified target")
    return target


def _element(target: ActionTarget | None) -> ElementRef:
    return _require(target).element
