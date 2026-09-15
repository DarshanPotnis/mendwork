"""Rung 0: find a step's target with its recorded selectors, or stop with evidence.

Each attempt waits for the page to settle, evaluates every selector, and then checks that
the DOM did not change while it was reading. Only a consistent snapshot can decide:

- the hits agree and the identity matches: resolved;
- the hits disagree, or several elements match: ambiguous;
- the hits agree but the identity differs: drifted;
- nothing matches: not found, after waiting for the page to change until the deadline.

Ambiguous and drifted verdicts stop at once when the snapshot was also quiet. On a busy
page they are retried until the deadline, in case the page was mid-transition. A page
that changes during every attempt stops with PageNeverStable.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.targets import IdentityReport, TargetEvidence
from mendwork.engine.errors import (
    AmbiguousTarget,
    MendworkError,
    PageNeverStable,
    TargetDrifted,
    TargetNotFound,
)
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import ElementIdentity, ElementRef
from mendwork.engine.replay.consensus import (
    Agreement,
    Disagreement,
    NoMatch,
    SeveralMatches,
    decide,
    number_elements,
    selector_outcome,
    selector_reports,
)
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.identity import identity_differences, recorded_identity
from mendwork.engine.replay.reports import TARGET_CONTEXT_KEY, identity_report
from mendwork.engine.safety.secret_scrub import SecretScrubber


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """The verified target: a pinned element, the selector that found it, and its identity."""

    element: ElementRef
    selector: Selector
    identity: ElementIdentity
    evidence: TargetEvidence


@dataclass(slots=True)
class _Attempt:
    outcome: ResolvedTarget | MendworkError
    pinned: list[ElementRef] = field(default_factory=list)


async def resolve_target(
    browser: BrowserPort,
    fingerprint: Fingerprint,
    *,
    deadline: Deadline,
    settle_timeout_ms: int,
    quiet_frames: int,
    scrubber: SecretScrubber,
    patient: bool = True,
) -> ResolvedTarget:
    """Resolve a fingerprint to one verified element, or raise with evidence.

    An impatient resolution decides on the first consistent reading of the settled page and never
    waits for the page to change: a pending patch's first try, made after the recorded target has
    already waited (ADR 0013).
    """
    started = deadline.timer.monotonic()
    last_stable: MendworkError | None = None
    while True:
        settling = await browser.wait_until_settled(
            quiet_frames=quiet_frames, timeout_ms=deadline.cap(settle_timeout_ms)
        )
        attempt = await _evaluate(browser, fingerprint, scrubber)
        keep: ElementRef | None = None
        try:
            epoch = await browser.dom_epoch(timeout_ms=deadline.timeout_ms())
            stable = epoch == settling.epoch
            if stable and isinstance(attempt.outcome, ResolvedTarget):
                keep = attempt.outcome.element
                return attempt.outcome
            if stable and isinstance(attempt.outcome, MendworkError):
                last_stable = attempt.outcome
                decisive = settling.quiet and not isinstance(attempt.outcome, TargetNotFound)
                if decisive or deadline.expired or not patient:
                    raise _waited(attempt.outcome, deadline, started)
            elif deadline.expired:
                raise _waited(last_stable or _never_stable(), deadline, started)
        finally:
            await browser.release([ref for ref in attempt.pinned if ref != keep])
        await browser.wait_for_dom_change(epoch, timeout_ms=deadline.timeout_ms())


async def _evaluate(
    browser: BrowserPort, fingerprint: Fingerprint, scrubber: SecretScrubber
) -> _Attempt:
    matches = [await browser.resolve_unique(selector) for selector in fingerprint.selectors]
    hits = [match.element for match in matches if match.element is not None]
    numbers = number_elements(await browser.group_identical(hits)) if hits else ()
    verdict = decide([selector_outcome(match) for match in matches], numbers)
    reports = selector_reports(fingerprint.selectors, matches, numbers)
    attempt = _Attempt(outcome=TargetNotFound("unevaluated"), pinned=list(hits))

    match verdict:
        case Agreement():
            element = hits[0]
            identity = await browser.identify(element, confirm=True)
            differences = identity_differences(fingerprint, identity)
            found = identity_report(identity, scrubber)
            evidence = TargetEvidence(
                selectors=reports,
                resolved_rank=verdict.rank,
                identity=found,
                differences=tuple(difference.value for difference in differences),
            )
            if not differences:
                attempt.outcome = ResolvedTarget(
                    element=element,
                    selector=fingerprint.selectors[verdict.rank],
                    identity=identity,
                    evidence=evidence,
                )
            else:
                attempt.outcome = TargetDrifted(
                    "the element the recorded selectors find no longer matches the recorded "
                    "identity",
                    reason="identity_changed",
                    recorded=recorded_identity(fingerprint).model_dump(mode="json"),
                    found=found.model_dump(mode="json"),
                    differences=list(evidence.differences),
                    **{TARGET_CONTEXT_KEY: evidence.model_dump(mode="json")},
                )
        case Disagreement():
            elements = await _identities(browser, hits, numbers, scrubber)
            evidence = TargetEvidence(selectors=reports, elements=elements)
            attempt.outcome = AmbiguousTarget(
                "the recorded selectors found different elements, so acting would be a guess",
                reason="selectors_disagree",
                groups=[list(group) for group in verdict.groups],
                **{TARGET_CONTEXT_KEY: evidence.model_dump(mode="json")},
            )
        case SeveralMatches():
            evidence = TargetEvidence(selectors=reports)
            attempt.outcome = AmbiguousTarget(
                "no recorded selector found exactly one element, and some found several",
                reason="several_matches",
                ranks=list(verdict.ranks),
                **{TARGET_CONTEXT_KEY: evidence.model_dump(mode="json")},
            )
        case NoMatch():
            evidence = TargetEvidence(selectors=reports)
            attempt.outcome = TargetNotFound(
                "no recorded selector matched a visible element",
                reason="no_match",
                **{TARGET_CONTEXT_KEY: evidence.model_dump(mode="json")},
            )
    return attempt


async def _identities(
    browser: BrowserPort,
    hits: Sequence[ElementRef],
    numbers: Sequence[int],
    scrubber: SecretScrubber,
) -> tuple[IdentityReport, ...]:
    reports: list[IdentityReport] = []
    for group in sorted(set(numbers)):
        representative = hits[list(numbers).index(group)]
        identity = await browser.identify(representative, confirm=False)
        reports.append(identity_report(identity, scrubber))
    return tuple(reports)


def _never_stable() -> PageNeverStable:
    return PageNeverStable(
        "the page kept changing, so the target could not be verified safely",
        reason="dom_changed_during_every_attempt",
    )


def _waited(error: MendworkError, deadline: Deadline, started: float) -> MendworkError:
    waited_ms = max(0, round((deadline.timer.monotonic() - started) * 1000))
    return type(error)(error.message, **{**error.context, "waited_ms": waited_ms})
