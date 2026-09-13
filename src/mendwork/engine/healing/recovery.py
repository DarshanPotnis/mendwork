"""Restoring the last known-good state after acting on a healed target failed verification.

The last known-good state is the page the failed step acted on, as the earlier steps built
it. Every step since the page's document was loaded began on that same document (the
segment), so the page is rebuilt by:

1. for a CAUTION step, clearing the field the failed fill typed into, so a value typed into
   the wrong field neither stays on screen nor gets submitted;
2. re-opening the URL the segment's first step began on, which discards unsaved state;
3. requiring the page to land on exactly that URL;
4. replaying the segment's earlier steps, each resolved by Rung 0 or by its own verified heal,
   with every checkpoint passing again.

Restoring is refused when it would replay an irreversible step. Any failure along the way
means the state could not be restored, and the step abstains; nothing is retried blindly.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.heals import RecoveryReport
from mendwork.engine.domain.steps import Step
from mendwork.engine.errors import InfrastructureError, MendworkError
from mendwork.engine.healing.run_state import RunHealState, StepStart
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import ElementRef, PlainText
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.navigation import navigate_with_retry
from mendwork.engine.safety.secret_scrub import SecretScrubber

ReplayStep = Callable[[StepStart, Deadline], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    """Which step failed, and what its failed attempt left behind."""

    index: int
    step: Step
    attempt: int
    reset: bool
    """Whether to clear what the failed action typed before restoring."""
    typed_into: ElementRef | None
    """The field the failed action typed into, if it was a fill."""
    deadline: Deadline


class StateRestorer:
    """Rebuilds the page a failed healed step acted on."""

    def __init__(
        self,
        *,
        browser: BrowserPort,
        state: RunHealState,
        config: ReplayConfig,
        timer: Timer,
        randomness: RandomSource,
        scrubber: SecretScrubber,
        log: structlog.stdlib.BoundLogger,
        replay: ReplayStep,
    ) -> None:
        self._browser = browser
        self._state = state
        self._config = config
        self._timer = timer
        self._randomness = randomness
        self._scrubber = scrubber
        self._log = log
        self._replay = replay

    async def restore(self, request: RestoreRequest) -> RecoveryReport:
        """Rebuild the page, or report why it could not be rebuilt."""
        segment = self._state.segment(request.index)
        first = segment[0]
        replayed = segment[:-1]
        report = RecoveryReport(
            after_attempt=request.attempt,
            url=self._scrubber.scrub_text(first.url),
            replayed=tuple(start.step.id for start in replayed),
            cleared_field=False,
            restored=False,
        )
        irreversible = next(
            (start for start in replayed if start.step.risk is RiskLevel.IRREVERSIBLE), None
        )
        if irreversible is not None:
            return report.model_copy(
                update={
                    "reason": f"restoring would repeat step {irreversible.step.id}, which is "
                    "irreversible"
                }
            )
        cleared = await self._clear(request) if request.reset else False
        report = report.model_copy(update={"cleared_field": cleared})
        try:
            await self._reopen(first, request.deadline)
            for start in replayed:
                await self._replay(start, request.deadline)
            epoch = await self._browser.dom_epoch(timeout_ms=request.deadline.timeout_ms())
        except InfrastructureError:
            raise
        except MendworkError as error:
            self._log.info(
                "heal_restore_failed", step_id=request.step.id, error_type=type(error).__name__
            )
            reason = f"{type(error).__name__}: {self._scrubber.scrub_text(error.message)}"
            return report.model_copy(update={"reason": reason})
        self._state.rebased(segment, epoch.document)
        self._log.info("heal_state_restored", step_id=request.step.id, replayed=len(replayed))
        return report.model_copy(update={"restored": True})

    async def _clear(self, request: RestoreRequest) -> bool:
        element = request.typed_into
        if element is None:
            return False
        state = await self._browser.actionability(element)
        if not (state.attached and state.editable):
            return False
        await self._browser.fill(
            element, PlainText(value=""), timeout_ms=request.deadline.timeout_ms()
        )
        return True

    async def _reopen(self, first: StepStart, deadline: Deadline) -> None:
        config = self._config
        await navigate_with_retry(
            self._browser,
            first.url,
            policy=config.retry,
            navigation_timeout_ms=config.navigation_timeout_ms,
            deadline=deadline,
            timer=self._timer,
            randomness=self._randomness,
            scrubber=self._scrubber,
            log=self._log,
        )
        await self._browser.wait_until_settled(
            quiet_frames=config.settle_quiet_frames,
            timeout_ms=deadline.cap(config.settle_timeout_ms),
        )
        landed = await self._browser.current_url()
        if landed != first.url:
            raise MendworkError(
                f"the page opened at {self._scrubber.scrub_text(landed)} instead of "
                f"{self._scrubber.scrub_text(first.url)}",
                reason="restore_landed_elsewhere",
            )
