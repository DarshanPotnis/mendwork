"""Running one step: resolve, check, watch, act, verify, record.

The order is the safety argument. The target is verified before anything touches the page;
events the action causes are watched for before the action; a new tab or window fails
the step; and every failure is recorded with the evidence that explains it.
"""

from dataclasses import dataclass, field
from datetime import datetime

import structlog

from mendwork.engine.domain.runs import (
    ArtifactName,
    CheckpointResult,
    ErrorReport,
    NavigationReport,
    RunId,
    StepArtifacts,
    StepResult,
    StepStatus,
    TargetEvidence,
)
from mendwork.engine.domain.steps import (
    ClickStep,
    FillStep,
    NavigateStep,
    PressStep,
    SelectStep,
    Step,
    step_target,
)
from mendwork.engine.errors import (
    CheckpointFailed,
    InfrastructureError,
    MendworkError,
    NavigationError,
    RunTimedOut,
)
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import ElementRef, SecretText, WatchId
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.evidence import EvidenceRecorder
from mendwork.engine.replay.navigation import navigate_with_retry
from mendwork.engine.replay.reports import error_report, target_evidence
from mendwork.engine.replay.rung0 import ResolvedTarget, resolve_target
from mendwork.engine.replay.values import TypedValue, ValueResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.engine.verification.checkpoints import (
    CheckpointContext,
    evaluate_checkpoint,
    evaluation_order,
    watch_kinds,
)
from mendwork.engine.verification.preaction import ensure_actionable


@dataclass(slots=True)
class _Progress:
    index: int
    step: Step
    started_at: datetime
    started: float
    target: TargetEvidence | None = None
    navigation: NavigationReport | None = None
    action_performed: bool = False
    checkpoints: list[CheckpointResult] = field(default_factory=list)
    download: ArtifactName | None = None
    pinned: list[ElementRef] = field(default_factory=list)
    finished: bool = False


class StepRunner:
    """Runs a run's steps one at a time against one browser session."""

    def __init__(
        self,
        *,
        browser: BrowserPort,
        artifacts: ArtifactStore,
        emitter: RunEmitter,
        values: ValueResolver,
        scrubber: SecretScrubber,
        clock: Clock,
        timer: Timer,
        randomness: RandomSource,
        config: ReplayConfig,
        run_id: RunId,
        run_deadline: Deadline,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        self._browser = browser
        self._emitter = emitter
        self._values = values
        self._scrubber = scrubber
        self._clock = clock
        self._timer = timer
        self._randomness = randomness
        self._config = config
        self._run_deadline = run_deadline
        self._log = log
        self._evidence = EvidenceRecorder(
            browser=browser,
            artifacts=artifacts,
            run_id=run_id,
            scrubber=scrubber,
            timeout_ms=config.step_timeout_ms,
            log=log,
        )
        self._progress: _Progress | None = None

    async def run(self, index: int, step: Step) -> StepResult:
        """Run one step and return its result; a step failure is a result, not an exception."""
        progress = _Progress(
            index=index, step=step, started_at=self._clock.now(), started=self._timer.monotonic()
        )
        self._progress = progress
        await self._emitter.step_started(index, step)
        try:
            await self._execute(progress)
            return await self._succeeded(progress)
        except MendworkError as error:
            return await self._failed(progress, self._attribute(progress, error))
        finally:
            await self._browser.release(progress.pinned)

    async def interrupted_by_run_timeout(self) -> StepResult | None:
        """The result of the step the run's time limit interrupted, if one was in progress."""
        progress = self._progress
        if progress is None or progress.finished:
            return None
        return await self._failed(progress, self._run_timeout(progress, interrupted=None))

    async def _execute(self, progress: _Progress) -> None:
        step = progress.step
        deadline = Deadline.after(self._timer, self._config.step_timeout_ms).earliest(
            self._run_deadline
        )
        resolved = await self._resolve(progress, deadline)
        kinds = watch_kinds(step.checkpoints)
        watch = await self._browser.watch(kinds) if kinds else None
        try:
            typed = await self._act(progress, resolved, deadline)
            # Let what the action caused arrive (a load, a banner, a new tab) before anything
            # is checked: a bounded wait for a quiet DOM, never a pause for time.
            await self._browser.wait_until_settled(
                quiet_frames=self._config.settle_quiet_frames,
                timeout_ms=deadline.cap(self._config.settle_timeout_ms),
            )
            await self._fail_on_new_pages()
            await self._verify(progress, watch, resolved, typed)
        finally:
            if watch is not None:
                await self._browser.unwatch(watch)

    async def _resolve(self, progress: _Progress, deadline: Deadline) -> ResolvedTarget | None:
        step = progress.step
        fingerprint = step_target(step)
        if fingerprint is None:
            return None
        resolved = await resolve_target(
            self._browser,
            fingerprint,
            deadline=deadline,
            settle_timeout_ms=self._config.settle_timeout_ms,
            quiet_frames=self._config.settle_quiet_frames,
            scrubber=self._scrubber,
        )
        progress.pinned.append(resolved.element)
        progress.target = resolved.evidence
        await self._emitter.target_resolved(progress.index, step.id, resolved.evidence)
        await ensure_actionable(
            self._browser,
            resolved.element,
            resolved.identity,
            step.action,
            deadline,
            self._scrubber,
        )
        return resolved

    async def _act(
        self, progress: _Progress, resolved: ResolvedTarget | None, deadline: Deadline
    ) -> TypedValue | None:
        step = progress.step
        typed: TypedValue | None = None
        match step:
            case NavigateStep():
                report = await navigate_with_retry(
                    self._browser,
                    self._values.plain(step.value),
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
                await self._emitter.action_performed(
                    progress.index, step, value_kind=step.value.kind, navigation=report
                )
                return None
            case ClickStep():
                await self._browser.click(_pinned(resolved), timeout_ms=deadline.timeout_ms())
            case FillStep():
                target = _require(resolved)
                typed = await self._values.typed(step.value)
                if isinstance(typed.text, SecretText):
                    self._evidence.secret_typed(progress.index, step.id, target.selector)
                await self._browser.fill(
                    target.element, typed.text, timeout_ms=deadline.timeout_ms()
                )
            case SelectStep():
                await self._browser.select_option(
                    _pinned(resolved),
                    self._values.plain(step.value),
                    timeout_ms=deadline.timeout_ms(),
                )
            case PressStep():
                element = resolved.element if resolved is not None else None
                await self._browser.press(element, step.key, timeout_ms=deadline.timeout_ms())
        progress.action_performed = True
        await self._emitter.action_performed(
            progress.index, step, value_kind=typed.kind if typed is not None else None
        )
        return typed

    async def _verify(
        self,
        progress: _Progress,
        watch: WatchId | None,
        resolved: ResolvedTarget | None,
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
            target=resolved.element if resolved is not None else None,
            expectation=typed.expectation if typed is not None else None,
        )
        for position, checkpoint in evaluation_order(step.checkpoints):
            outcome = await evaluate_checkpoint(context, position, checkpoint)
            if outcome.download is not None:
                progress.download = await self._evidence.keep_download(
                    outcome.download, progress.index, step.id
                )
            result = outcome.result
            progress.checkpoints.append(result)
            await self._emitter.checkpoint(progress.index, step.id, result)
            if not result.passed:
                await self._fail_on_new_pages()
                raise CheckpointFailed(
                    f"checkpoint {position + 1} ({result.kind}) did not pass: {result.reason}",
                    checkpoint_index=position,
                    kind=result.kind.value,
                    reason=result.reason,
                    detail=result.detail,
                )
        await self._fail_on_new_pages()

    async def _fail_on_new_pages(self) -> None:
        opened = await self._browser.take_opened_pages()
        if opened:
            raise NavigationError(
                "the action opened a new tab or window; workflows that span several pages at "
                "once are not supported",
                reason="new_page_opened",
                pages=opened,
            )

    async def _succeeded(self, progress: _Progress) -> StepResult:
        problems: list[str] = []
        screenshot = await self._evidence.screenshot(
            progress.index, progress.step.id, problems, fatal=True
        )
        artifacts = StepArtifacts(
            screenshot=screenshot, download=progress.download, capture_errors=tuple(problems)
        )
        result = self._result(progress, StepStatus.SUCCEEDED, artifacts)
        progress.finished = True
        await self._emitter.step_succeeded(result)
        return result

    async def _failed(self, progress: _Progress, error: MendworkError) -> StepResult:
        step = progress.step
        problems: list[str] = []
        screenshot = await self._evidence.screenshot(progress.index, step.id, problems, fatal=False)
        dom = await self._evidence.dom_snapshot(progress.index, step.id, problems)
        trace, withheld = await self._evidence.trace(problems)
        artifacts = StepArtifacts(
            screenshot=screenshot,
            dom_snapshot=dom,
            trace=trace,
            trace_withheld=withheld,
            download=progress.download,
            capture_errors=tuple(problems),
        )
        report = error_report(error, self._scrubber)
        result = self._result(
            progress,
            StepStatus.FAILED,
            artifacts,
            error=report,
            target=progress.target or target_evidence(error),
        )
        progress.finished = True
        await self._emitter.step_failed(result)
        self._log.warning(
            "step_failed",
            step_id=step.id,
            error_type=report.type,
            reason=str(report.context.get("reason")),
            action_performed=progress.action_performed,
        )
        return result

    def _result(
        self,
        progress: _Progress,
        status: StepStatus,
        artifacts: StepArtifacts,
        *,
        error: ErrorReport | None = None,
        target: TargetEvidence | None = None,
    ) -> StepResult:
        elapsed = max(0.0, self._timer.monotonic() - progress.started)
        return StepResult(
            step_id=progress.step.id,
            index=progress.index,
            action=progress.step.action,
            status=status,
            started_at=progress.started_at,
            duration_ms=round(elapsed * 1000),
            target=target or progress.target,
            navigation=progress.navigation,
            action_performed=progress.action_performed,
            checkpoints=tuple(progress.checkpoints),
            error=error,
            artifacts=artifacts,
        )

    def _attribute(self, progress: _Progress, error: MendworkError) -> MendworkError:
        # A wait cut short by the run's deadline failed because the run ran out of time,
        # whatever the wait was for.
        if self._run_deadline.expired and not isinstance(error, RunTimedOut | InfrastructureError):
            return self._run_timeout(progress, interrupted=error)
        return error

    def _run_timeout(self, progress: _Progress, interrupted: MendworkError | None) -> RunTimedOut:
        context: dict[str, object] = {
            "reason": "run_timeout",
            "run_timeout_ms": self._config.run_timeout_ms,
        }
        if interrupted is not None:
            context |= {**interrupted.context, "interrupted_error": type(interrupted).__name__}
            context["reason"] = "run_timeout"
        return RunTimedOut(
            f"the run exceeded its time limit of {self._config.run_timeout_ms} ms during step "
            f"{progress.step.id}",
            **context,
        )


def _require(resolved: ResolvedTarget | None) -> ResolvedTarget:
    if resolved is None:
        raise MendworkError("a step with a target reached its action without a verified target")
    return resolved


def _pinned(resolved: ResolvedTarget | None) -> ElementRef:
    return _require(resolved).element
