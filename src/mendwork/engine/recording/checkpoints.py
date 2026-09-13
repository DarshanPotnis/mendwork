"""Proposing checkpoints from what a step changed, and keeping only those that pass.

Proposals come from observations before and after the step:

- the URL's path changed: ``url_matches``, a host-agnostic regex of the new path;
- a heading or landmark became visible: ``element_visible`` (up to three alternatives,
  headings first, so one that is not unique can give way to the next);
- a live region's text changed without a navigation: ``text_present``;
- a download completed: ``download_completed`` with the exact file name;
- the step submitted a form: ``no_error_banner``;
- a fill: ``field_has_value``.

Each proposal is verified with the same evaluation replay uses. One that does not pass is
dropped with its reason, never written.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from mendwork.engine.domain.checkpoints import Checkpoint, DownloadCompleted
from mendwork.engine.domain.enums import CheckpointKind, UrlMatchMode
from mendwork.engine.domain.limits import CHECKPOINTS_MAX_ITEMS
from mendwork.engine.domain.recording import DroppedCheckpoint
from mendwork.engine.ports.browser_types import DownloadObservation
from mendwork.engine.ports.recording_types import Landmark, PageObservation
from mendwork.engine.verification.checkpoints import CheckpointContext, evaluate_checkpoint
from mendwork.engine.verification.text import normalize_whitespace

ALTERNATIVES_MAX: Final = 3
HEADING: Final = "heading"
LANDMARK_ROLES: Final = frozenset(
    {
        HEADING,
        "alertdialog",
        "banner",
        "complementary",
        "contentinfo",
        "dialog",
        "form",
        "main",
        "navigation",
        "region",
        "search",
    }
)
_CHECKPOINT: Final[TypeAdapter[Checkpoint]] = TypeAdapter(Checkpoint)


@dataclass(frozen=True, slots=True)
class Proposal:
    """One thing a step should be checked for, with alternatives tried in order."""

    kind: CheckpointKind
    alternatives: tuple[Checkpoint, ...]


def url_pattern(url: str) -> str | None:
    """A regex for any URL with this path on any host, allowing a query and a fragment."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return None
    return rf"https?://[^?#]+{re.escape(parts.path or '/')}(?:[?#].*)?"


def propose_after_action(
    before: PageObservation,
    after: PageObservation,
    *,
    navigated: bool,
    download: DownloadObservation | None,
    submits_form: bool,
) -> tuple[Proposal, ...]:
    """Checkpoints for a click or key step."""
    proposals: list[Proposal] = []
    if _path(before.url) != _path(after.url):
        pattern = url_pattern(after.url)
        if pattern is not None:
            _add(
                proposals,
                CheckpointKind.URL_MATCHES,
                [{"kind": "url_matches", "mode": UrlMatchMode.REGEX, "pattern": pattern}],
            )
    _add_landmarks(proposals, new_landmarks(before, after))
    text = None if navigated else new_live_text(before, after)
    if text is not None:
        _add(proposals, CheckpointKind.TEXT_PRESENT, [{"kind": "text_present", "text": text}])
    if download is not None and download.path is not None and download.failure is None:
        _add(
            proposals,
            CheckpointKind.DOWNLOAD_COMPLETED,
            [
                {
                    "kind": "download_completed",
                    "filename_pattern": re.escape(download.suggested_filename),
                }
            ],
        )
    if submits_form:
        _add(proposals, CheckpointKind.NO_ERROR_BANNER, [{"kind": "no_error_banner"}])
    return tuple(proposals)


def propose_after_navigation(after: PageObservation) -> tuple[Proposal, ...]:
    """Checkpoints for a navigate step: the page's first heading or landmark."""
    proposals: list[Proposal] = []
    _add_landmarks(proposals, new_landmarks(None, after))
    return tuple(proposals)


def new_landmarks(before: PageObservation | None, after: PageObservation) -> tuple[Landmark, ...]:
    """Visible headings and named landmarks present after but not before, headings first."""
    seen = set() if before is None else {_landmark_key(item) for item in before.landmarks}
    fresh: list[Landmark] = []
    for item in after.landmarks:
        key = _landmark_key(item)
        if item.role in LANDMARK_ROLES and key[1] and key not in seen:
            seen.add(key)
            fresh.append(item)
    return tuple(sorted(fresh, key=lambda item: item.role != HEADING))


def new_live_text(before: PageObservation, after: PageObservation) -> str | None:
    """The first live region text present after the step that was not there before."""
    remaining = Counter(normalize_whitespace(text) for text in before.live_texts)
    for text in after.live_texts:
        flat = normalize_whitespace(text)
        if not flat:
            continue
        if remaining[flat]:
            remaining[flat] -= 1
            continue
        return flat
    return None


async def keep_passing(
    proposals: Sequence[Proposal],
    context: CheckpointContext,
    *,
    download: DownloadObservation | None = None,
) -> tuple[tuple[Checkpoint, ...], tuple[DroppedCheckpoint, ...]]:
    """The checkpoints that pass now, in proposal order, and why the others were dropped."""
    kept: list[Checkpoint] = []
    dropped: list[DroppedCheckpoint] = []
    for proposal in proposals:
        reason = "no alternative could be written"
        for candidate in proposal.alternatives:
            passed, why = await _passes(candidate, len(kept), context, download)
            if passed:
                kept.append(candidate)
                break
            reason = why
        else:
            dropped.append(DroppedCheckpoint(kind=proposal.kind, reason=reason))
        if len(kept) == CHECKPOINTS_MAX_ITEMS:
            break
    return tuple(kept), tuple(dropped)


async def _passes(
    checkpoint: Checkpoint,
    position: int,
    context: CheckpointContext,
    download: DownloadObservation | None,
) -> tuple[bool, str]:
    if isinstance(checkpoint, DownloadCompleted):
        # The download was already collected to learn its name; it is checked against that.
        if download is None or download.path is None:
            return False, "no download completed"
        matched = re.fullmatch(checkpoint.filename_pattern, download.suggested_filename)
        return matched is not None, "the file name did not match"
    outcome = await evaluate_checkpoint(context, position, checkpoint)
    return outcome.result.passed, f"did not pass ({outcome.result.reason})"


def _add_landmarks(proposals: list[Proposal], landmarks: Sequence[Landmark]) -> None:
    documents: list[dict[str, object]] = [
        {
            "kind": "element_visible",
            "selector": {"strategy": "role_name", "role": item.role, "name": item.name},
        }
        for item in landmarks[: ALTERNATIVES_MAX * 2]
    ]
    _add(proposals, CheckpointKind.ELEMENT_VISIBLE, documents, limit=ALTERNATIVES_MAX)


def _add(
    proposals: list[Proposal],
    kind: CheckpointKind,
    documents: Sequence[dict[str, object]],
    *,
    limit: int = 1,
) -> None:
    alternatives: list[Checkpoint] = []
    for document in documents:
        try:
            alternatives.append(_CHECKPOINT.validate_python(document))
        except ValidationError:
            continue
        if len(alternatives) == limit:
            break
    if alternatives:
        proposals.append(Proposal(kind=kind, alternatives=tuple(alternatives)))


def _landmark_key(item: Landmark) -> tuple[str, str]:
    return item.role, normalize_whitespace(item.name).casefold()


def _path(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.path or "/"
