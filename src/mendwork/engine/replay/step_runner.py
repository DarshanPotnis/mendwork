"""Running one step: resolve, check, watch, act, verify, record, and heal when Rung 0 cannot.

The order is the safety argument. The target is verified before anything touches the page;
events the action causes are watched for before the action; a new tab or window fails the
step; and every failure is recorded with the evidence that explains it. When the recorded
selectors cannot safely proceed, the heal ladder takes over (``step_healing``), and a healed
target passes exactly the same checks and checkpoints before the step counts as done.
"""

import structlog

from mendwork.engine.domain.runs import (
    ErrorReport,
    RunId,
    StepArtifacts,
    StepResult,
    StepStatus,
)
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.domain.targets import TargetEvidence
from mendwork.engine.errors import (
    ApprovalRequired,
    InfrastructureError,
    MendworkError,
    NeedsReview,
    RunTimedOut,
)
from mendwork.engine.healing.context import LadderContext
from mendwork.engine.healing.ladder import is_healable
from mendwork.engine.healing.model_rung import ModelChooser
from mendwork.engine.healing.recovery import StateRestorer
from mendwork.engine.healing.run_state import RunHealState, StepStart
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.evidence import EvidenceRecorder
from mendwork.engine.replay.progress import StepProgress
from mendwork.engine.replay.reports import error_report, target_evidence
from mendwork.engine.replay.rung0 import resolve_target
from mendwork.engine.replay.step_actions import ActionTarget, StepActions
from mendwork.engine.replay.step_healing import StepHealer
from mendwork.engine.replay.values import ValueResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber


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
        chooser: ModelChooser | None = None,
    ) -> None:
        self._browser = browser
        self._emitter = emitter
        self._scrubber = scrubber
        self._clock = clock
        self._timer = timer
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
        self._state = RunHealState()
        self._actions = StepActions(
            browser=browser,
            emitter=emitter,
            values=values,
            evidence=self._evidence,
            scrubber=scrubber,
            timer=timer,
            randomness=randomness,
            config=config,
            run_deadline=run_deadline,
            log=log,
        )
        self._healer = StepHealer(
            browser=browser,
            actions=self._actions,
            emitter=emitter,
            state=self._state,
            restorer=StateRestorer(
                browser=browser,
                state=self._state,
                config=config,
                timer=timer,
                randomness=randomness,
                scrubber=scrubber,
                log=log,
                replay=self._replay_quietly,
            ),
            ladder=LadderContext(
                browser=browser,
                config=config.healing,
                settle_timeout_ms=config.settle_timeout_ms,
                quiet_frames=config.settle_quiet_frames,
                scrubber=scrubber,
                chooser=chooser,
            ),
            config=config,
            timer=timer,
            run_deadline=run_deadline,
            scrubber=scrubber,
            log=log,
        )
        self._progress: StepProgress | None = None

    async def run(self, index: int, step: Step) -> StepResult:
        """Run one step and return its result; a step failure is a result, not an exception."""
        progress = StepProgress(
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

    async def _execute(self, progress: StepProgress) -> None:
        step = progress.step
        deadline = Deadline.after(self._timer, self._config.step_timeout_ms).earliest(
            self._run_deadline
        )
        epoch = await self._browser.dom_epoch(timeout_ms=deadline.timeout_ms())
        url = await self._browser.current_url()
        self._state.started(StepStart(progress.index, step, epoch.document, url))
        if step_target(step) is None:
            await self._actions.perform(progress, None, deadline)
            return
        try:
            target = await self._resolve(progress, deadline)
        except MendworkError as failure:
            if self._run_deadline.expired or not is_healable(failure):
                raise
            await self._healer.heal(progress, failure)
            return
        await self._actions.perform(progress, target, deadline)

    async def _resolve(self, progress: StepProgress, deadline: Deadline) -> ActionTarget:
        step = progress.step
        fingerprint = step_target(step)
        if fingerprint is None:
            raise MendworkError("a step without a target has nothing to resolve", step_id=step.id)
        resolved = await resolve_target(
            self._browser,
            fingerprint,
            deadline=deadline,
            settle_timeout_ms=self._config.settle_timeout_ms,
            quiet_frames=self._config.settle_quiet_frames,
            scrubber=self._scrubber,
        )
        progress.pinned.append(resolved.element)
        if not progress.quiet:
            progress.target = resolved.evidence
            await self._emitter.target_resolved(progress.index, step.id, resolved.evidence)
        return ActionTarget(resolved.element, resolved.identity, resolved.selector)

    async def _replay_quietly(self, start: StepStart, restore_deadline: Deadline) -> None:
        """Replay an earlier step while a page is restored: no events, no new result."""
        step = start.step
        progress = StepProgress(
            index=start.index,
            step=step,
            started_at=self._clock.now(),
            started=self._timer.monotonic(),
            quiet=True,
        )
        deadline = Deadline.after(self._timer, self._config.step_timeout_ms).earliest(
            restore_deadline
        )
        try:
            target: ActionTarget | None = None
            if step_target(step) is not None:
                try:
                    target = await self._resolve(progress, deadline)
                except MendworkError as failure:
                    if not is_healable(failure):
                        raise
                    target = await self._healer.reuse(progress, failure, deadline)
            await self._actions.perform(progress, target, deadline)
        finally:
            await self._browser.release(progress.pinned)

    async def _succeeded(self, progress: StepProgress) -> StepResult:
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

    async def _failed(self, progress: StepProgress, error: MendworkError) -> StepResult:
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
            _stopping_status(error),
            artifacts,
            error=report,
            target=progress.target or target_evidence(error),
        )
        progress.finished = True
        await self._emitter.step_failed(result)
        self._log.warning(
            "step_failed",
            step_id=step.id,
            status=result.status.value,
            error_type=report.type,
            reason=str(report.context.get("reason")),
            action_performed=progress.action_performed,
        )
        return result

    def _result(
        self,
        progress: StepProgress,
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
            heal=progress.heal_report(),
        )

    def _attribute(self, progress: StepProgress, error: MendworkError) -> MendworkError:
        # A wait cut short by the run's deadline failed because the run ran out of time,
        # whatever the wait was for.
        if self._run_deadline.expired and not isinstance(error, RunTimedOut | InfrastructureError):
            return self._run_timeout(progress, interrupted=error)
        return error

    def _run_timeout(
        self, progress: StepProgress, interrupted: MendworkError | None
    ) -> RunTimedOut:
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


def _stopping_status(error: MendworkError) -> StepStatus:
    if isinstance(error, ApprovalRequired):
        return StepStatus.AWAITING_APPROVAL
    if isinstance(error, NeedsReview):
        return StepStatus.NEEDS_REVIEW
    return StepStatus.FAILED
