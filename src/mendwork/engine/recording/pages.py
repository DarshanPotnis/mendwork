"""Recording NAVIGATE steps: the start URL, and pages the person opened from the browser."""

from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import urlsplit

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.recording import DraftStep, InputHint, LiteralDraft
from mendwork.engine.domain.values import check_http_url
from mendwork.engine.errors import NavigationError
from mendwork.engine.ports.recording_types import NavigationRecord
from mendwork.engine.recording.checkpoints import keep_passing, propose_after_navigation
from mendwork.engine.recording.context import CaptureContext
from mendwork.engine.recording.describe import navigate_words, step_id
from mendwork.engine.recording.failures import UnusableReason, unusable
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.navigation import navigate_with_retry
from mendwork.engine.safety.risk import RiskSignals, classify_risk


@dataclass(frozen=True, slots=True)
class PageStep:
    """A recorded NAVIGATE step, and the last navigation it accounted for."""

    draft: DraftStep
    attributed_through: int


async def capture_start(context: CaptureContext, url: str, *, taken: Collection[str]) -> PageStep:
    """Open the start URL as the recording's first step."""
    config = context.config
    retry = config.retry
    budget = (config.navigation_timeout_ms + retry.max_delay_ms) * retry.max_attempts
    try:
        await navigate_with_retry(
            context.browser,
            url,
            policy=retry,
            navigation_timeout_ms=config.navigation_timeout_ms,
            deadline=Deadline.after(context.timer, budget),
            timer=context.timer,
            randomness=context.randomness,
            scrubber=context.scrubber,
            log=context.log,
        )
    except NavigationError as error:
        raise unusable(
            UnusableReason.START_URL_UNREACHABLE,
            f"the start URL could not be opened: {error.message}",
            navigation_reason=error.context.get("reason"),
        ) from error
    return await _page_step(context, url, index=0, taken=taken, hint=InputHint.START_URL, floor=0)


async def capture_browser_navigation(
    context: CaptureContext, record: NavigationRecord, *, index: int, taken: Collection[str]
) -> PageStep | None:
    """A NAVIGATE step for a page opened from the browser; None if it is not a web page."""
    try:
        check_http_url(record.url)
    except ValueError:
        return None
    return await _page_step(
        context, record.url, index=index, taken=taken, hint=None, floor=record.sequence
    )


async def _page_step(
    context: CaptureContext,
    url: str,
    *,
    index: int,
    taken: Collection[str],
    hint: InputHint | None,
    floor: int,
) -> PageStep:
    browser = context.browser
    await context.settle()
    await context.fail_on_new_pages()
    after = await browser.observe_page(limit=context.config.landmarks_max)
    checkpoints, dropped = await keep_passing(
        propose_after_navigation(after), context.checkpoints()
    )
    description, intent = navigate_words(url, after.title)
    assessment = classify_risk(RiskSignals(action=ActionType.NAVIGATE), context.config.risk)
    draft = DraftStep(
        index=index,
        step_id=step_id("open", after.title or urlsplit(url).path or "page", taken),
        action=ActionType.NAVIGATE,
        description=description,
        intent=intent,
        risk=assessment.level,
        risk_reasons=assessment.reasons,
        value=LiteralDraft(value=url, hint=hint),
        checkpoints=checkpoints,
        dropped_checkpoints=dropped,
    )
    return PageStep(draft=draft, attributed_through=max(floor, after.navigation))
