"""Capturing a verified heal's element as the recorder would record it (ADR 0013).

Just before a healed target is acted on, while its element is still pinned and the page is the one
the heal was accepted on, the recorder's own ``TargetRecorder`` derives its selectors and
fingerprint and proves the fingerprint resolves back to it at Rung 0. That fingerprint is the target
a new version's step would carry, so a rerun on the same page finds it without healing. A masked
screenshot around the element is kept for the run's report.

Capturing reads the page and never acts on it, and never fails the step: an element that cannot be
recorded only means its heal cannot become a version.
"""

from typing import Final

import structlog

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.patches import CaptureProblem, FoundTarget, ImageBox
from mendwork.engine.errors import RecordingUnusable
from mendwork.engine.ports.browser_types import ElementRef
from mendwork.engine.recording.failures import UnusableReason
from mendwork.engine.recording.target_context import TargetCaptureContext
from mendwork.engine.recording.targets import TargetRecorder
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.evidence import EvidenceRecorder
from mendwork.engine.safety.secret_scrub import SecretScrubber

_PROBLEMS: Final = {
    UnusableReason.NO_SELECTOR: CaptureProblem.NO_SELECTOR,
    UnusableReason.IDENTITY_UNCONFIRMED: CaptureProblem.IDENTITY_UNCONFIRMED,
    UnusableReason.UNRECORDABLE_TARGET: CaptureProblem.UNRECORDABLE_TARGET,
    UnusableReason.ELEMENT_GONE: CaptureProblem.ELEMENT_GONE,
    UnusableReason.PAGE_NEVER_STABLE: CaptureProblem.PAGE_NEVER_STABLE,
}


class HealCapture:
    """Fingerprints healed elements for one run."""

    def __init__(
        self,
        *,
        targets: TargetCaptureContext,
        evidence: EvidenceRecorder,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        self._targets = targets
        self._evidence = evidence
        self._log = log

    async def capture(
        self, index: int, step_id: StepId, element: ElementRef, deadline: Deadline
    ) -> FoundTarget:
        """The healed element's fingerprint and screenshot, or why it has no fingerprint."""
        view = await self._evidence.element_view(index, step_id, element)
        screenshot, box = (None, None) if view is None else view
        image_box = (
            None if box is None else ImageBox(x=box.x, y=box.y, width=box.width, height=box.height)
        )
        try:
            recorded = await TargetRecorder(self._targets).record(element, deadline=deadline)
        except RecordingUnusable as error:
            problem = capture_problem(error)
            self._log.info("heal_not_capturable", step_id=step_id, problem=problem.value)
            return FoundTarget(
                problem=problem,
                detail=self._targets.scrubber.scrub_text(error.message),
                screenshot=screenshot,
                box=image_box,
            )
        if carries_secret(recorded.fingerprint, self._targets.scrubber):
            self._log.warning("heal_target_holds_secret", step_id=step_id)
            return FoundTarget(
                problem=CaptureProblem.SECRET_IN_TARGET,
                detail="the element's text holds a value resolved from a secret",
                screenshot=screenshot,
                box=image_box,
            )
        return FoundTarget(fingerprint=recorded.fingerprint, screenshot=screenshot, box=image_box)


def capture_problem(error: RecordingUnusable) -> CaptureProblem:
    """The capture problem a recording failure means."""
    try:
        reason = UnusableReason(str(error.context.get("reason")))
    except ValueError:
        return CaptureProblem.UNRECORDABLE_TARGET
    return _PROBLEMS.get(reason, CaptureProblem.UNRECORDABLE_TARGET)


def carries_secret(fingerprint: Fingerprint, scrubber: SecretScrubber) -> bool:
    """Whether any of a fingerprint's text holds a value the run resolved from a secret."""
    text = fingerprint.model_dump_json()
    return scrubber.scrub_text(text) != text
