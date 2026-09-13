"""Rung 1: find the recorded identity with selectors the recording did not keep.

The alternates are evaluated with Rung 0's own consensus inside one consistent reading. Rung 1
accepts only the recorded identity itself:

- the alternates agree on one element;
- its role and name (or tag and type) match the fingerprint exactly, confirmed by Playwright;
- it reaches the accept threshold, so the same name in a completely different place with
  nothing else in common is not enough;
- no safety rule refuses it, and it did not already fail verification.

No margin is needed: uniqueness by identity rules out a look-alike. An element the alternates
agree on whose identity drifted is handed to Rung 2 as a candidate.
"""

from dataclasses import dataclass

from mendwork.engine.domain.heals import CandidateOrigin, HealAttemptReport, RungOutcome
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.targets import SelectorReport, TargetEvidence
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.healing.alternates import alternate_selectors
from mendwork.engine.healing.checks import safety_rejection
from mendwork.engine.healing.context import (
    AcceptedHeal,
    ClimbRequest,
    LadderContext,
    scored_candidate,
)
from mendwork.engine.healing.scoring import score_candidate
from mendwork.engine.healing.snapshot import read_consistently
from mendwork.engine.ports.browser_types import ElementIdentity, ElementRef
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.ports.element_types import ElementFacts
from mendwork.engine.replay.consensus import (
    Agreement,
    NoMatch,
    Verdict,
    decide,
    number_elements,
    selector_outcome,
    selector_reports,
)
from mendwork.engine.replay.identity import identity_differences
from mendwork.engine.replay.reports import identity_report


@dataclass(frozen=True, slots=True)
class Rung1Result:
    """Rung 1's report, its accepted heal if any, and a selector to seed Rung 2 with."""

    report: HealAttemptReport
    accepted: AcceptedHeal | None = None
    seed: Selector | None = None


@dataclass(frozen=True, slots=True)
class _Found:
    """The one element the alternates agreed on, read in the same snapshot."""

    element: ElementRef
    identity: ElementIdentity
    facts: ElementFacts


@dataclass(frozen=True, slots=True)
class _Reading:
    verdict: Verdict
    reports: tuple[SelectorReport, ...]
    hits: tuple[ElementRef, ...]
    found: _Found | None


async def run_rung1(context: LadderContext, request: ClimbRequest) -> Rung1Result:
    """Try every alternate selector; accept only the recorded identity, found uniquely."""
    browser = context.browser
    selectors = alternate_selectors(request.fingerprint)
    if not selectors:
        return Rung1Result(_report(request, RungOutcome.NOT_FOUND, TargetEvidence(selectors=())))

    async def read() -> _Reading:
        matches = [await browser.resolve_unique(selector) for selector in selectors]
        hits = tuple(match.element for match in matches if match.element is not None)
        numbers = number_elements(await browser.group_identical(hits)) if hits else ()
        verdict = decide([selector_outcome(match) for match in matches], numbers)
        found: _Found | None = None
        if isinstance(verdict, Agreement):
            identity = await browser.identify(hits[0], confirm=True)
            try:
                found = _Found(hits[0], identity, await browser.element_facts(hits[0]))
            except TargetNotFound:
                found = None
        return _Reading(verdict, selector_reports(selectors, matches, numbers), hits, found)

    async def release(reading: _Reading) -> None:
        await browser.release(reading.hits)

    reading = await read_consistently(
        browser,
        request.deadline,
        settle_timeout_ms=context.settle_timeout_ms,
        quiet_frames=context.quiet_frames,
        read=read,
        release=release,
    )
    verdict = reading.verdict
    if not isinstance(verdict, Agreement) or reading.found is None:
        await browser.release(reading.hits)
        outcome = (
            RungOutcome.NOT_FOUND
            if isinstance(verdict, NoMatch | Agreement)
            else RungOutcome.AMBIGUOUS
        )
        return Rung1Result(_report(request, outcome, TargetEvidence(selectors=reading.reports)))
    found = reading.found
    await browser.release([hit for hit in reading.hits if hit != found.element])
    return await _judge(context, request, reading.reports, verdict, found, selectors[verdict.rank])


async def _judge(
    context: LadderContext,
    request: ClimbRequest,
    reports: tuple[SelectorReport, ...],
    verdict: Agreement,
    found: _Found,
    seed: Selector,
) -> Rung1Result:
    browser = context.browser
    config = context.config
    fingerprint = request.fingerprint
    differences = identity_differences(fingerprint, found.identity)
    evidence = TargetEvidence(
        selectors=reports,
        resolved_rank=verdict.rank,
        identity=identity_report(found.identity, context.scrubber),
        differences=tuple(difference.value for difference in differences),
    )
    if differences:
        await browser.release([found.element])
        return Rung1Result(_report(request, RungOutcome.DRIFTED, evidence), seed=seed)
    candidate = LiveCandidate(element=found.element, identity=found.identity, facts=found.facts)
    scored = score_candidate(
        fingerprint,
        candidate,
        CandidateOrigin.RUNG1,
        config,
        safety_rejection(request.step, fingerprint, candidate, config.vocabulary),
    )
    report = _report(request, RungOutcome.RESOLVED, evidence).model_copy(
        update={
            "candidates": (scored_candidate(scored, "c1", context.scrubber, found.identity),),
            "considered": 1,
            "score": scored.score,
            "threshold": config.accept_threshold,
        }
    )
    if scored.signature in request.excluded:
        await browser.release([found.element])
        return Rung1Result(report.model_copy(update={"outcome": RungOutcome.NOT_FOUND}))
    if scored.rejection is not None or scored.score < config.accept_threshold:
        await browser.release([found.element])
        outcome = (
            RungOutcome.TOP_REJECTED
            if scored.rejection is not None
            else RungOutcome.BELOW_THRESHOLD
        )
        return Rung1Result(report.model_copy(update={"outcome": outcome}), seed=seed)
    report = report.model_copy(update={"chosen": "c1"})
    return Rung1Result(
        report, AcceptedHeal(rung=1, scored=scored, identity=found.identity, report=report)
    )


def _report(
    request: ClimbRequest, outcome: RungOutcome, target: TargetEvidence
) -> HealAttemptReport:
    return HealAttemptReport(rung=1, attempt=request.attempt, outcome=outcome, target=target)
