"""Recording a click or a key: verify the target, perform the action, observe what it did.

The page held the person's gesture back. The recorder verifies the target first, then lets
its own action through (arm), performs it with the same primitives replay uses, holds the
page back again (disarm), and waits for the page to settle. Navigations committed in that
window belong to this step. A control that was hidden or disabled did nothing when the
person clicked it, so that gesture is ignored rather than recorded.
"""

from collections.abc import Collection
from dataclasses import dataclass

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.recording import DraftStep, IgnoredReason
from mendwork.engine.errors import TargetNotActionable, TargetNotFound
from mendwork.engine.ports.browser_types import DownloadObservation, ElementRef, WatchKind
from mendwork.engine.ports.element_types import ElementFacts
from mendwork.engine.ports.recording import element_key
from mendwork.engine.ports.recording_types import (
    ClickCapture,
    PageObservation,
    PressCapture,
    PressKey,
)
from mendwork.engine.recording.checkpoints import keep_passing, propose_after_action
from mendwork.engine.recording.context import CaptureContext
from mendwork.engine.recording.describe import step_id, target_words
from mendwork.engine.recording.failures import UnusableReason, unusable
from mendwork.engine.recording.navigation import browser_navigations
from mendwork.engine.recording.targets import RecordedTarget, TargetRecorder
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.safety.risk import RiskSignals, classify_risk


@dataclass(frozen=True, slots=True)
class GestureStep:
    """A recorded click or key step, and the last navigation its window accounted for."""

    draft: DraftStep
    attributed_through: int


async def capture_gesture(
    context: CaptureContext,
    capture: ClickCapture | PressCapture,
    *,
    index: int,
    taken: Collection[str],
) -> GestureStep | IgnoredReason:
    """Record a held-back click or key, or say why it was ignored."""
    browser = context.browser
    element: ElementRef | None = None
    if capture.ref.element is not None:
        element = await browser.pin_capture(capture.ref)
        if element is None:
            raise unusable(
                UnusableReason.ELEMENT_GONE,
                "the element the gesture was made on left the page before it could be recorded",
            )
    try:
        target: RecordedTarget | None = None
        if element is not None:
            state = await browser.actionability(element)
            if not (state.attached and state.visible and state.enabled):
                return IgnoredReason.NOT_ACTIONABLE
            target = await TargetRecorder(context.targets()).record(element)
        return await _perform(context, capture, element, target, index=index, taken=taken)
    finally:
        if element is not None:
            await browser.release([element])


async def _perform(
    context: CaptureContext,
    capture: ClickCapture | PressCapture,
    element: ElementRef | None,
    target: RecordedTarget | None,
    *,
    index: int,
    taken: Collection[str],
) -> GestureStep | IgnoredReason:
    browser = context.browser
    config = context.config
    before = await browser.observe_page(limit=config.landmarks_max)
    watch = await browser.watch(frozenset({WatchKind.DOWNLOAD}))
    try:
        if not await browser.arm(capture.ref):
            raise unusable(
                UnusableReason.ELEMENT_GONE,
                "the page changed documents before the gesture could be performed",
            )
        deadline = Deadline.after(context.timer, config.step_timeout_ms)
        try:
            if isinstance(capture, ClickCapture) and element is not None:
                await browser.click(element, timeout_ms=deadline.timeout_ms())
            elif isinstance(capture, PressCapture):
                await browser.press(element, capture.key.value, timeout_ms=deadline.timeout_ms())
        except (TargetNotActionable, TargetNotFound):
            return IgnoredReason.NOT_ACTIONABLE
        finally:
            await browser.disarm(capture.ref)
        await context.settle()
        await context.fail_on_new_pages()
        download: DownloadObservation | None = None
        if await browser.downloads_started(watch):
            # Finishing the download is part of performing the step, not of checking it.
            download = await browser.next_download(watch, timeout_ms=config.step_timeout_ms)
        after = await browser.observe_page(limit=config.landmarks_max)
        navigations = await browser.navigations_since(before.navigation)
        if browser_navigations(navigations):
            raise unusable(
                UnusableReason.BROWSER_NAVIGATION_DURING_STEP,
                "the browser went to another page while a step was being recorded",
            )
        facts = target.facts if target is not None else None
        submits = _submits_form(capture, facts)
        draft = await _draft(
            context,
            capture,
            target,
            index=index,
            taken=taken,
            before=before,
            after=after,
            navigated=bool(navigations),
            download=download,
            submits=submits,
        )
    finally:
        await browser.unwatch(watch)
    through = max([before.navigation, *(record.sequence for record in navigations)])
    return GestureStep(draft=draft, attributed_through=through)


async def _draft(
    context: CaptureContext,
    capture: ClickCapture | PressCapture,
    target: RecordedTarget | None,
    *,
    index: int,
    taken: Collection[str],
    before: PageObservation,
    after: PageObservation,
    navigated: bool,
    download: DownloadObservation | None,
    submits: bool,
) -> DraftStep:
    identity = target.identity if target is not None else None
    facts = target.facts if target is not None else None
    role = identity.role if identity is not None else None
    name = identity.name if identity is not None else None
    tag = facts.tag if facts is not None else ""
    completed = download is not None and download.path is not None and download.failure is None
    key = capture.key if isinstance(capture, PressCapture) else None
    action = ActionType.CLICK if key is None else ActionType.PRESS
    assessment = classify_risk(
        RiskSignals(
            action=action,
            role=role,
            name=name,
            form_submit=submits,
            form_has_password=bool(facts and facts.form_has_password),
            link=role == "link"
            and facts is not None
            and facts.href is not None
            and key in {None, PressKey.ENTER},
            downloaded=completed,
        ),
        context.config.risk,
    )
    proposals = propose_after_action(
        before, after, navigated=navigated, download=download, submits_form=submits
    )
    checkpoints, dropped = await keep_passing(proposals, context.checkpoints(), download=download)
    description, intent = target_words(action, role=role, name=name, tag=tag, key=key)
    prefix = "click" if key is None else f"press_{key.value.lower()}"
    return DraftStep(
        index=index,
        step_id=step_id(prefix, name or role or tag, taken),
        action=action,
        description=description,
        intent=intent,
        risk=assessment.level,
        risk_reasons=assessment.reasons,
        target=target.fingerprint if target is not None else None,
        key=key.value if key is not None else None,
        checkpoints=checkpoints,
        selector=target.choice if target is not None else None,
        dropped_selectors=target.dropped if target is not None else (),
        dropped_checkpoints=dropped,
        element_key=element_key(capture.ref),
    )


def _submits_form(capture: ClickCapture | PressCapture, facts: ElementFacts | None) -> bool:
    if facts is None:
        return False
    if isinstance(capture, ClickCapture):
        return facts.form_submit
    if capture.key is PressKey.ENTER:
        return facts.form_submit or (facts.in_form and facts.text_entry)
    return capture.key is PressKey.SPACE and facts.form_submit
