"""What every capture needs: the browser, tuning, time, and the shared waits and checks."""

from dataclasses import dataclass

import structlog

from mendwork.engine.domain.limits import TIMEOUT_MS_MAX
from mendwork.engine.ports.browser_types import ElementRef, FieldExpectation
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.recording import RecordingBrowser
from mendwork.engine.ports.timer import Timer
from mendwork.engine.recording.config import RecordingConfig
from mendwork.engine.recording.failures import UnusableReason, unusable
from mendwork.engine.recording.target_context import TargetCaptureContext
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.engine.verification.checkpoints import CheckpointContext


@dataclass(frozen=True, slots=True)
class CaptureContext:
    """One recording's browser and tuning, shared by every step capture."""

    browser: RecordingBrowser
    config: RecordingConfig
    timer: Timer
    randomness: RandomSource
    scrubber: SecretScrubber
    log: structlog.stdlib.BoundLogger

    def targets(self) -> TargetCaptureContext:
        """What fingerprinting a recorded target reads with."""
        return TargetCaptureContext(
            browser=self.browser,
            timer=self.timer,
            scrubber=self.scrubber,
            step_timeout_ms=self.config.step_timeout_ms,
            settle_timeout_ms=self.config.settle_timeout_ms,
            settle_quiet_frames=self.config.settle_quiet_frames,
            scope_ancestors_max=self.config.scope_ancestors_max,
        )

    def checkpoints(
        self, *, target: ElementRef | None = None, expectation: FieldExpectation | None = None
    ) -> CheckpointContext:
        """Evaluation context for verifying proposed checkpoints right now."""
        return CheckpointContext(
            browser=self.browser,
            timer=self.timer,
            # Each checkpoint has its own timeout; recording has no overall run deadline.
            run_deadline=Deadline.after(self.timer, TIMEOUT_MS_MAX),
            default_timeout_ms=self.config.checkpoint_timeout_ms,
            scrubber=self.scrubber,
            watch=None,
            target=target,
            expectation=expectation,
        )

    async def settle(self) -> None:
        """Let what an action caused arrive before anything is observed."""
        await self.browser.wait_until_settled(
            quiet_frames=self.config.settle_quiet_frames,
            timeout_ms=self.config.settle_timeout_ms,
        )

    async def fail_on_new_pages(self) -> None:
        """End the recording if a new tab or window opened: multi-page flows are unsupported."""
        opened = await self.browser.take_opened_pages()
        if opened:
            raise unusable(
                UnusableReason.NEW_PAGE_OPENED,
                "a new tab or window opened; workflows that span several pages at once cannot "
                "be recorded",
                pages=opened,
            )
