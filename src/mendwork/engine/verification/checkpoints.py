"""Checkpoint evaluation: what each kind means, and the order they run in.

- ``download_completed`` and ``response_received`` observe events the action causes, so
  the step watches for them before its action (``watch_kinds``).
- Checkpoints run in the order written, except ``no_error_banner``, which runs last and
  only once: waiting for something not to appear would be a sleep.
- Every waiting checkpoint uses its own ``timeout_ms`` or the runtime default, and never
  runs past the run's deadline.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from mendwork.engine.domain.checkpoints import (
    Checkpoint,
    DownloadCompleted,
    ElementVisible,
    FieldHasValue,
    NoErrorBanner,
    ResponseReceived,
    TextPresent,
    UrlMatches,
)
from mendwork.engine.domain.enums import CheckpointKind
from mendwork.engine.domain.runs import CheckpointResult
from mendwork.engine.errors import MendworkError
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import (
    DownloadObservation,
    ElementRef,
    FieldExpectation,
    WatchId,
    WatchKind,
)
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.reports import detail
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.engine.verification.polling import wait_for_condition
from mendwork.engine.verification.text import contains_text, first_error_banner


@dataclass(frozen=True, slots=True)
class CheckpointContext:
    """What checkpoint evaluation needs from the step that ran."""

    browser: BrowserPort
    timer: Timer
    run_deadline: Deadline
    default_timeout_ms: int
    scrubber: SecretScrubber
    watch: WatchId | None
    target: ElementRef | None
    expectation: FieldExpectation | None


@dataclass(frozen=True, slots=True)
class CheckpointOutcome:
    """A checkpoint's result, plus the completed download it observed, if any."""

    result: CheckpointResult
    download: DownloadObservation | None = None


def watch_kinds(checkpoints: Sequence[Checkpoint]) -> frozenset[WatchKind]:
    """The events to watch for before the step's action."""
    kinds: set[WatchKind] = set()
    for checkpoint in checkpoints:
        if isinstance(checkpoint, DownloadCompleted):
            kinds.add(WatchKind.DOWNLOAD)
        elif isinstance(checkpoint, ResponseReceived):
            kinds.add(WatchKind.RESPONSE)
    return frozenset(kinds)


def evaluation_order(checkpoints: Sequence[Checkpoint]) -> tuple[tuple[int, Checkpoint], ...]:
    """Checkpoints with their written positions: as written, banners last."""
    indexed = list(enumerate(checkpoints))
    return tuple(
        [item for item in indexed if not isinstance(item[1], NoErrorBanner)]
        + [item for item in indexed if isinstance(item[1], NoErrorBanner)]
    )


def _passed(index: int, kind: CheckpointKind, text: str | None = None) -> CheckpointOutcome:
    return CheckpointOutcome(CheckpointResult(index=index, kind=kind, passed=True, detail=text))


def _failed(
    index: int, kind: CheckpointKind, reason: str, text: str | None = None
) -> CheckpointOutcome:
    return CheckpointOutcome(
        CheckpointResult(index=index, kind=kind, passed=False, reason=reason, detail=text)
    )


async def evaluate_checkpoint(
    context: CheckpointContext, index: int, checkpoint: Checkpoint
) -> CheckpointOutcome:
    """Evaluate one checkpoint after the step's action."""
    match checkpoint:
        case NoErrorBanner():
            return await _no_error_banner(context, index, checkpoint)
        case UrlMatches():
            return await _url_matches(context, index, checkpoint)
        case ElementVisible():
            return await _element_visible(context, index, checkpoint)
        case TextPresent():
            return await _text_present(context, index, checkpoint)
        case DownloadCompleted():
            return await _download_completed(context, index, checkpoint)
        case ResponseReceived():
            return await _response_received(context, index, checkpoint)
        case FieldHasValue():
            return await _field_has_value(context, index, checkpoint)


async def _no_error_banner(
    context: CheckpointContext, index: int, checkpoint: NoErrorBanner
) -> CheckpointOutcome:
    kind = checkpoint.kind
    if checkpoint.selector is None:
        banner = first_error_banner(await context.browser.visible_alert_texts())
        if banner is None:
            return _passed(index, kind)
        return _failed(index, kind, "error_banner_visible", detail(banner, context.scrubber))
    count = await context.browser.count_visible(checkpoint.selector)
    if count == 0:
        return _passed(index, kind)
    return _failed(
        index, kind, "error_banner_visible", f"{count} visible element(s) match the banner selector"
    )


async def _url_matches(
    context: CheckpointContext, index: int, checkpoint: UrlMatches
) -> CheckpointOutcome:
    deadline = _deadline(context, checkpoint.timeout_ms)
    matched = await context.browser.wait_for_url(checkpoint, timeout_ms=deadline.timeout_ms())
    url = detail(await context.browser.current_url(), context.scrubber)
    if matched:
        return _passed(index, checkpoint.kind, url)
    return _failed(index, checkpoint.kind, "timeout", url)


async def _element_visible(
    context: CheckpointContext, index: int, checkpoint: ElementVisible
) -> CheckpointOutcome:
    browser = context.browser

    async def visible() -> bool:
        match = await browser.resolve_unique(checkpoint.selector)
        if match.element is None:
            return False
        await browser.release([match.element])
        return True

    deadline = _deadline(context, checkpoint.timeout_ms)
    if await wait_for_condition(browser, deadline, visible):
        return _passed(index, checkpoint.kind)
    return _failed(index, checkpoint.kind, "timeout")


async def _text_present(
    context: CheckpointContext, index: int, checkpoint: TextPresent
) -> CheckpointOutcome:
    browser = context.browser

    async def present() -> bool:
        return contains_text(await browser.visible_text(), checkpoint.text)

    deadline = _deadline(context, checkpoint.timeout_ms)
    if await wait_for_condition(browser, deadline, present):
        return _passed(index, checkpoint.kind)
    return _failed(index, checkpoint.kind, "timeout")


async def _download_completed(
    context: CheckpointContext, index: int, checkpoint: DownloadCompleted
) -> CheckpointOutcome:
    kind = checkpoint.kind
    deadline = _deadline(context, checkpoint.timeout_ms)
    download = await context.browser.next_download(
        _require_watch(context), timeout_ms=deadline.timeout_ms()
    )
    if download is None:
        return _failed(index, kind, "timeout")
    name = detail(download.suggested_filename, context.scrubber)
    if download.failure is not None or download.path is None:
        return _failed(index, kind, "download_failed", name)
    if re.fullmatch(checkpoint.filename_pattern, download.suggested_filename) is None:
        return _failed(index, kind, "filename_mismatch", name)
    return CheckpointOutcome(
        CheckpointResult(index=index, kind=kind, passed=True, detail=name), download=download
    )


async def _response_received(
    context: CheckpointContext, index: int, checkpoint: ResponseReceived
) -> CheckpointOutcome:
    deadline = _deadline(context, checkpoint.timeout_ms)
    response = await context.browser.next_response(
        _require_watch(context), checkpoint, timeout_ms=deadline.timeout_ms()
    )
    if response is None:
        return _failed(index, checkpoint.kind, "timeout")
    return _passed(
        index, checkpoint.kind, detail(f"{response.status} {response.url}", context.scrubber)
    )


async def _field_has_value(
    context: CheckpointContext, index: int, checkpoint: FieldHasValue
) -> CheckpointOutcome:
    if context.target is None or context.expectation is None:
        raise MendworkError("field_has_value was evaluated outside a fill step")
    deadline = _deadline(context, checkpoint.timeout_ms)
    check = await context.browser.wait_for_field_value(
        context.target, context.expectation, timeout_ms=deadline.timeout_ms()
    )
    if check.matches:
        return _passed(index, checkpoint.kind)
    return _failed(index, checkpoint.kind, "field_empty" if check.empty else "value_differs")


def _deadline(context: CheckpointContext, timeout_ms: int | None) -> Deadline:
    own = Deadline.after(context.timer, timeout_ms or context.default_timeout_ms)
    return own.earliest(context.run_deadline)


def _require_watch(context: CheckpointContext) -> WatchId:
    if context.watch is None:
        raise MendworkError("an event checkpoint was evaluated without watching for its events")
    return context.watch
