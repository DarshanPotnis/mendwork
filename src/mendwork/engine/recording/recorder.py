"""The recorder: opens the start URL, turns what a person does into verified draft steps.

Page events are handled strictly in order, one at a time. A held-back click or key is
performed by the recorder once its target is verified; a committed field is read (or
recorded as a secret); a page the person opened from the browser becomes a NAVIGATE step.
Interactions that cannot be recorded are ignored with a notice and recording continues.
Anything that would make the workflow wrong ends the recording with RecordingUnusable.

Stopping finishes the step in progress, asks the page to commit fields still being edited,
records what that produced, and ends.
"""

import structlog

from mendwork.engine.domain.recording import (
    DraftStep,
    IgnoredReason,
    InteractionIgnored,
    NavigationIgnored,
    Recording,
    RecordingNotice,
    StepRecorded,
)
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.recording import (
    RecordingBrowser,
    RecordingLauncher,
    RecordingObserver,
    StopSignal,
)
from mendwork.engine.ports.recording_types import (
    ClickCapture,
    FillCapture,
    IgnoredCapture,
    NavigationCommitted,
    PageEvent,
    PageRestored,
    PressCapture,
    ProtocolViolation,
    SelectCapture,
    SessionClosed,
)
from mendwork.engine.ports.timer import Timer
from mendwork.engine.recording.config import RecordingConfig
from mendwork.engine.recording.context import CaptureContext
from mendwork.engine.recording.failures import UnusableReason, unusable
from mendwork.engine.recording.fields import capture_field
from mendwork.engine.recording.gestures import capture_gesture
from mendwork.engine.recording.hygiene import merge_fill
from mendwork.engine.recording.navigation import OutsideNavigation, classify_outside
from mendwork.engine.recording.pages import capture_browser_navigation, capture_start
from mendwork.engine.safety.secret_scrub import SecretScrubber


class _Progress:
    """The steps and notices so far, and how far navigations have been accounted for."""

    def __init__(self) -> None:
        self.steps: tuple[DraftStep, ...] = ()
        self.notices: list[RecordingNotice] = []
        self.attributed_through = 0

    @property
    def taken(self) -> frozenset[str]:
        return frozenset(step.step_id for step in self.steps)

    @property
    def next_index(self) -> int:
        return len(self.steps)


class Recorder:
    """Records one workflow from a person's interactions."""

    def __init__(
        self,
        *,
        launcher: RecordingLauncher,
        observer: RecordingObserver,
        timer: Timer,
        randomness: RandomSource,
        config: RecordingConfig,
    ) -> None:
        self._launcher = launcher
        self._observer = observer
        self._timer = timer
        self._randomness = randomness
        self._config = config

    async def record(self, start_url: str, stop: StopSignal) -> Recording:
        """Record until the person stops or closes the browser.

        Raises RecordingUnusable when the recording cannot produce a replayable workflow.
        """
        log = structlog.stdlib.get_logger("mendwork.recording")
        progress = _Progress()
        async with self._launcher.recording_session() as browser:
            context = CaptureContext(
                browser=browser,
                config=self._config,
                timer=self._timer,
                randomness=self._randomness,
                scrubber=SecretScrubber(),
                log=log,
            )
            start = await capture_start(context, start_url, taken=progress.taken)
            await self._add(progress, start.draft)
            progress.attributed_through = start.attributed_through
            await self._run(context, browser, progress, stop)
        if len(progress.steps) < 2:
            raise unusable(
                UnusableReason.NOTHING_RECORDED,
                "nothing was recorded after the start page opened",
            )
        log.info("recording_finished", step_count=len(progress.steps))
        return Recording(start_url=start_url, steps=progress.steps, notices=tuple(progress.notices))

    async def _run(
        self,
        context: CaptureContext,
        browser: RecordingBrowser,
        progress: _Progress,
        stop: StopSignal,
    ) -> None:
        while True:
            event = await browser.next_event(stop)
            if isinstance(event, SessionClosed):
                return
            if event is not None:
                await self._handle(context, progress, event)
                continue
            for pending in await browser.flush_pending(timeout_ms=self._config.step_timeout_ms):
                if isinstance(pending, SessionClosed):
                    return
                await self._handle(context, progress, pending)
            return

    async def _handle(self, context: CaptureContext, progress: _Progress, event: PageEvent) -> None:
        match event:
            case ClickCapture() | PressCapture():
                try:
                    outcome = await capture_gesture(
                        context, event, index=progress.next_index, taken=progress.taken
                    )
                finally:
                    await context.browser.finish_capture(event.ref)
                if isinstance(outcome, IgnoredReason):
                    await self._notice(progress, InteractionIgnored(reason=outcome))
                    return
                await self._add(progress, outcome.draft)
                progress.attributed_through = max(
                    progress.attributed_through, outcome.attributed_through
                )
            case FillCapture() | SelectCapture():
                draft = await capture_field(
                    context, event, index=progress.next_index, taken=progress.taken
                )
                await self._add(progress, draft)
            case IgnoredCapture():
                during = progress.steps[-1].index if event.reason is IgnoredReason.BUSY else None
                await self._notice(
                    progress, InteractionIgnored(reason=event.reason, during_step=during)
                )
            case NavigationCommitted():
                await self._navigation(context, progress, event)
            case PageRestored():
                raise unusable(
                    UnusableReason.PAGE_RESTORED,
                    "the browser restored a page from its back-forward cache, with state the "
                    "recording cannot account for",
                )
            case ProtocolViolation():
                raise unusable(
                    UnusableReason.PROTOCOL_VIOLATION,
                    "the page sent the recorder a message it does not understand",
                )
            case SessionClosed():
                return

    async def _navigation(
        self, context: CaptureContext, progress: _Progress, event: NavigationCommitted
    ) -> None:
        record = event.record
        match classify_outside(record, progress.attributed_through):
            case OutsideNavigation.ALREADY_ATTRIBUTED:
                return
            case OutsideNavigation.IGNORE:
                progress.attributed_through = record.sequence
                await self._notice(progress, NavigationIgnored(url=record.url))
            case OutsideNavigation.STEP:
                page = await capture_browser_navigation(
                    context, record, index=progress.next_index, taken=progress.taken
                )
                progress.attributed_through = max(
                    record.sequence,
                    progress.attributed_through,
                    page.attributed_through if page is not None else 0,
                )
                if page is None:
                    await self._notice(progress, NavigationIgnored(url=record.url))
                else:
                    await self._add(progress, page.draft)

    async def _add(self, progress: _Progress, draft: DraftStep) -> None:
        progress.steps, replaced = merge_fill(progress.steps, draft)
        await self._notice(progress, StepRecorded(step=progress.steps[-1], replaced=replaced))

    async def _notice(self, progress: _Progress, notice: RecordingNotice) -> None:
        progress.notices.append(notice)
        await self._observer.notify(notice)
