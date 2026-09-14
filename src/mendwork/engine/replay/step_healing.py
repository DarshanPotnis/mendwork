"""Healing a step: climb the ladder, gate the heal, act, verify, and recover from a failed heal.

For each pass through the ladder:

1. every rung's report is recorded and emitted; nothing accepted means the step abstains;
2. an accepted heal is gated, in this order:
   - the step must have a checkpoint that can prove the heal, or it abstains;
   - an irreversible step never acts on a heal: it stops with a proposal for approval;
   - the step must be within its heal attempt limit (one for an authentication step);
3. the healed target goes through the same pre-action checks, action, and checkpoints as a
   target Rung 0 verified; the heal is proven only when every checkpoint passes;
4. a healed target that fails its pre-action checks was never acted on: it is excluded and
   the ladder runs again;
5. a heal that fails its checkpoints is excluded. An irreversible step ends in NEEDS_REVIEW
   and is never retried; otherwise, within the attempt limit, the page is restored to its
   last known-good state and Rung 0 runs again on it.

A heal Rung 3's model chose is held to exactly the same gates, action, and checkpoints.
"""

from collections.abc import Callable

import structlog

from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import AbstentionReason, Verification
from mendwork.engine.domain.runs import CheckpointResult
from mendwork.engine.domain.steps import FillStep, Step, step_target
from mendwork.engine.domain.targets import TargetEvidence
from mendwork.engine.errors import (
    CheckpointFailed,
    HealAbstained,
    MendworkError,
    NeedsReview,
    TargetDrifted,
    TargetNotActionable,
    TargetNotFound,
)
from mendwork.engine.healing.candidates import CandidateSignature
from mendwork.engine.healing.checks import mask_selector
from mendwork.engine.healing.context import AcceptedHeal, ClimbRequest, LadderContext
from mendwork.engine.healing.explain import abstained
from mendwork.engine.healing.gates import HealStop, before_acting, over_limit
from mendwork.engine.healing.ladder import ClimbResult, climb, is_healable
from mendwork.engine.healing.recovery import RestoreRequest, StateRestorer
from mendwork.engine.healing.run_state import RunHealState
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import ElementRef
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.heal_record import HealRecorder, settle_pending
from mendwork.engine.replay.progress import StepProgress
from mendwork.engine.replay.reports import identity_report
from mendwork.engine.replay.rung0 import resolve_target
from mendwork.engine.replay.step_actions import ActionTarget, StepActions
from mendwork.engine.safety.heal_policy import (
    FailedHealRecovery,
    heal_may_act,
    recovery_after_failed_heal,
)
from mendwork.engine.safety.secret_scrub import SecretScrubber

_BEFORE_ACTION_ERRORS = (TargetNotActionable, TargetDrifted, TargetNotFound)
_MODEL_RUNG = 3


class StepHealer:
    """Heals the steps of one run."""

    def __init__(
        self,
        *,
        browser: BrowserPort,
        actions: StepActions,
        emitter: RunEmitter,
        state: RunHealState,
        restorer: StateRestorer,
        ladder: LadderContext,
        config: ReplayConfig,
        timer: Timer,
        run_deadline: Deadline,
        scrubber: SecretScrubber,
        log: structlog.stdlib.BoundLogger,
        may_act: Callable[[RiskLevel], bool] = heal_may_act,
    ) -> None:
        self._browser = browser
        self._actions = actions
        self._emitter = emitter
        self._state = state
        self._restorer = restorer
        self._ladder = ladder
        self._config = config
        self._timer = timer
        self._run_deadline = run_deadline
        self._scrubber = scrubber
        self._log = log
        self._may_act = may_act
        self._record = HealRecorder(emitter)

    async def heal(self, progress: StepProgress, failure: MendworkError) -> None:
        """Heal a step whose recorded selectors could not safely proceed, or raise why not.

        Raises HealAbstained, ApprovalRequired, or NeedsReview, or the error a healed target's
        action failed with once it was performed.
        """
        step = progress.step
        fingerprint = _target(step)
        heal_deadline = Deadline.after(self._timer, self._config.healing.timeout_ms).earliest(
            self._run_deadline
        )
        excluded: set[CandidateSignature] = set()
        attempt = 0
        current: MendworkError | None = failure
        while current is not None:
            attempt += 1
            if heal_deadline.expired:
                progress.abstention = AbstentionReason.HEAL_TIMED_OUT
                raise HealAbstained(
                    "healing used all of its time before a heal was proven",
                    reason=AbstentionReason.HEAL_TIMED_OUT.value,
                    heal_timeout_ms=self._config.healing.timeout_ms,
                )
            request = ClimbRequest(
                step=step,
                fingerprint=fingerprint,
                attempt=attempt,
                excluded=frozenset(excluded),
                deadline=heal_deadline,
                heal_actions_used=self._state.heal_actions(step.id),
            )
            result = await climb(self._ladder, request, current)
            current = await self._attempt(progress, result, current, excluded, heal_deadline)

    async def reuse(
        self, progress: StepProgress, failure: MendworkError, deadline: Deadline
    ) -> ActionTarget:
        """The target of a step replayed during a restore: only its own verified heal will do.

        A heal Rung 3 verified is found again by its signature, without asking a model.
        """
        step = progress.step
        remembered = self._state.verified(step.id)
        if remembered is None:
            raise failure
        request = ClimbRequest(
            step=step,
            fingerprint=_target(step),
            attempt=1,
            excluded=frozenset(),
            deadline=deadline,
            reuse=remembered.signature if remembered.rung == _MODEL_RUNG else None,
        )
        accepted = (await climb(self._ladder, request, failure)).accepted
        if accepted is None or accepted.scored.signature != remembered.signature:
            if accepted is not None:
                await self._browser.release([accepted.scored.candidate.element])
            raise HealAbstained(
                f"step {step.id} could not be replayed: its verified heal is no longer the element "
                "the ladder finds",
                reason="verified_heal_not_found",
            )
        candidate = accepted.scored.candidate
        progress.pinned.append(candidate.element)
        return ActionTarget(candidate.element, accepted.identity, mask_selector(candidate.facts))

    async def _attempt(
        self,
        progress: StepProgress,
        result: ClimbResult,
        failure: MendworkError,
        excluded: set[CandidateSignature],
        heal_deadline: Deadline,
    ) -> MendworkError | None:
        accepted = result.accepted
        if accepted is None:
            await self._record.attempted(progress, result.reports)
            raise self._abstained(progress, result)
        blocked = self._blocked(progress, accepted)
        if blocked is not None:
            await self._browser.release([accepted.scored.candidate.element])
            await self._record.attempted(progress, result.reports)
            raise blocked
        pending = accepted.report.model_copy(update={"verification": Verification.PENDING})
        await self._record.attempted(progress, (*result.reports[:-1], pending))
        step = progress.step
        candidate = accepted.scored.candidate
        progress.pinned.append(candidate.element)
        progress.target = TargetEvidence(
            selectors=(),
            identity=identity_report(accepted.identity, self._scrubber),
            healed_rung=accepted.rung,
        )
        await self._emitter.target_resolved(progress.index, step.id, progress.target)
        deadline = Deadline.after(self._timer, self._config.step_timeout_ms).earliest(heal_deadline)
        target = ActionTarget(candidate.element, accepted.identity, mask_selector(candidate.facts))
        try:
            await self._actions.perform(progress, target, deadline)
        except CheckpointFailed as failed:
            self._state.count_heal_action(step.id)
            await self._record.verified(progress, _checkpoint(progress, failed))
            excluded.add(accepted.scored.signature)
            await self._recover(progress, accepted, heal_deadline)
            return await self._resolve_again(progress, heal_deadline)
        except _BEFORE_ACTION_ERRORS:
            if progress.action_performed:
                raise
            settle_pending(progress, Verification.NOT_PERFORMED)
            excluded.add(accepted.scored.signature)
            await self._release(progress, candidate.element)
            progress.target = None
            return failure
        self._state.count_heal_action(step.id)
        await self._record.verified(progress, None)
        self._state.remember_verified(step.id, accepted.scored.signature, accepted.rung)
        progress.healed_rung = accepted.rung
        self._log.info(
            "heal_verified", step_id=step.id, rung=accepted.rung, score=accepted.scored.score
        )
        return None

    def _blocked(self, progress: StepProgress, accepted: AcceptedHeal) -> MendworkError | None:
        stop = before_acting(
            progress.step,
            accepted,
            used=self._state.heal_actions(progress.step.id),
            config=self._config.healing,
            may_act=self._may_act,
            scrubber=self._scrubber,
        )
        if stop is None:
            return None
        _apply(progress, stop)
        return stop.error

    async def _recover(
        self, progress: StepProgress, accepted: AcceptedHeal, heal_deadline: Deadline
    ) -> None:
        step = progress.step
        recovery = recovery_after_failed_heal(step.risk)
        if recovery is FailedHealRecovery.NEEDS_REVIEW:
            raise NeedsReview(
                "an irreversible action ran on a healed target and its checkpoints did not pass; "
                "it is never retried",
                reason="irreversible_heal_unverified",
                rung=accepted.rung,
            )
        over = over_limit(
            step,
            accepted,
            used=self._state.heal_actions(step.id),
            config=self._config.healing,
        )
        if over is not None:
            _apply(progress, over)
            raise over.error
        element = accepted.scored.candidate.element
        report = await self._restorer.restore(
            RestoreRequest(
                index=progress.index,
                step=step,
                attempt=accepted.report.attempt,
                reset=recovery is FailedHealRecovery.RESET_THEN_RESTORE,
                typed_into=element if isinstance(step, FillStep) else None,
                deadline=heal_deadline,
            )
        )
        progress.recoveries.append(report)
        await self._emitter.state_restored(progress.index, step.id, report)
        await self._release(progress, element)
        if not report.restored:
            progress.abstention = AbstentionReason.RESTORE_FAILED
            raise HealAbstained(
                "a heal failed its checkpoints, and the page could not be restored to try "
                f"another: {report.reason}",
                reason=AbstentionReason.RESTORE_FAILED.value,
            )
        progress.forget_action()

    async def _resolve_again(
        self, progress: StepProgress, heal_deadline: Deadline
    ) -> MendworkError | None:
        """Rung 0 on the restored page: its failure feeds the next pass through the ladder."""
        step = progress.step
        deadline = Deadline.after(self._timer, self._config.step_timeout_ms).earliest(heal_deadline)
        try:
            resolved = await resolve_target(
                self._browser,
                _target(step),
                deadline=deadline,
                settle_timeout_ms=self._config.settle_timeout_ms,
                quiet_frames=self._config.settle_quiet_frames,
                scrubber=self._scrubber,
            )
        except MendworkError as failure:
            if not is_healable(failure):
                raise
            return failure
        progress.pinned.append(resolved.element)
        progress.target = resolved.evidence
        await self._emitter.target_resolved(progress.index, step.id, resolved.evidence)
        target = ActionTarget(resolved.element, resolved.identity, resolved.selector)
        await self._actions.perform(progress, target, deadline)
        return None

    def _abstained(self, progress: StepProgress, result: ClimbResult) -> HealAbstained:
        reason = result.abstention or AbstentionReason.NO_CANDIDATES
        progress.abstention = reason
        deciding = result.deciding or result.reports[-1]
        self._log.info(
            "heal_abstained", step_id=progress.step.id, reason=reason.value, rung=deciding.rung
        )
        return abstained(reason, deciding, self._config.healing)

    async def _release(self, progress: StepProgress, element: ElementRef) -> None:
        kept = [pinned for pinned in progress.pinned if pinned != element]
        released = [pinned for pinned in progress.pinned if pinned == element]
        progress.pinned[:] = kept
        await self._browser.release(released)


def _apply(progress: StepProgress, stop: HealStop) -> None:
    progress.abstention = stop.abstention
    progress.proposal = stop.proposal


def _target(step: Step) -> Fingerprint:
    fingerprint = step_target(step)
    if fingerprint is None:
        raise MendworkError("only a step with a target can be healed", step_id=step.id)
    return fingerprint


def _checkpoint(progress: StepProgress, failed: CheckpointFailed) -> CheckpointResult:
    """The checkpoint result a CheckpointFailed was raised for: the last one recorded."""
    if not progress.checkpoints:
        raise MendworkError(
            "a checkpoint failure was raised without its result", error=failed.message
        )
    return progress.checkpoints[-1]
