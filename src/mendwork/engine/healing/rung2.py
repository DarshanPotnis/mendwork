"""Rung 2: compare every live candidate with the recorded fingerprint; accept only a clear winner.

One consistent reading holds the page's scan and the elements earlier rungs found (the
drifted Rung 0 match, a Rung 1 hit), which join as candidates like any other. Then:

- a page with more candidates than the configured cap is not healed: scoring part of a page
  cannot prove the margin, because an element left out could be the target or a look-alike;
- candidates the action cannot receive, duplicates, and candidates that already failed
  verification are dropped;
- every other candidate is scored and checked against the safety rules, and the accept rule
  decides; the winner's identity must then be confirmed by Playwright.

Every element but an accepted winner is released before returning, except when a model is
configured and the ranking declined in a way Rung 3 takes up: then the whole ranking stays
pinned for Rung 3, which releases it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.heals import (
    CandidateOrigin,
    HealAttemptReport,
    RejectionReason,
    RungOutcome,
    SafetyRejection,
)
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.healing.acceptance import Accepted, Declined, decide
from mendwork.engine.healing.candidates import compatible, found_kind, recorded_kind
from mendwork.engine.healing.checks import safety_rejection
from mendwork.engine.healing.context import (
    AcceptedHeal,
    ClimbRequest,
    LadderContext,
    scored_candidate,
)
from mendwork.engine.healing.eligibility import takes_up
from mendwork.engine.healing.scoring import ScoredElement, rank, score_candidate
from mendwork.engine.healing.snapshot import read_consistently_at
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import DomEpoch, ElementIdentity, ElementRef
from mendwork.engine.ports.candidate_types import CandidateQuery, CandidateScan, LiveCandidate
from mendwork.engine.safety.heal_kinds import compare_kinds

Seed = tuple[Selector, CandidateOrigin]
Pooled = tuple[LiveCandidate, CandidateOrigin]


@dataclass(frozen=True, slots=True)
class Rung2Decline:
    """A declined ranking kept pinned for Rung 3, with the report and the epoch it was read at."""

    ranked: tuple[ScoredElement, ...]
    report: HealAttemptReport
    epoch: DomEpoch


@dataclass(frozen=True, slots=True)
class Rung2Result:
    """Rung 2's report, and its accepted heal or the ranking it leaves to Rung 3, if any."""

    report: HealAttemptReport
    accepted: AcceptedHeal | None = None
    declined: Rung2Decline | None = None


@dataclass(frozen=True, slots=True)
class _Pool:
    scan: CandidateScan
    seeded: tuple[Pooled, ...]

    def elements(self) -> list[ElementRef]:
        return [candidate.element for candidate, _ in self.seeded] + [
            candidate.element for candidate in self.scan.candidates
        ]


async def run_rung2(
    context: LadderContext, request: ClimbRequest, seeds: Sequence[Seed]
) -> Rung2Result:
    """Score every candidate on the page and accept a clear, safe, confirmed winner."""
    browser = context.browser
    config = context.config

    async def read() -> _Pool:
        query = CandidateQuery(action=request.step.action, limit=config.candidates_max)
        scan = await browser.scan_candidates(query)
        return _Pool(scan, await _seeded(browser, seeds))

    async def release(pool: _Pool) -> None:
        await browser.release(pool.elements())

    pool, epoch = await read_consistently_at(
        browser,
        request.deadline,
        settle_timeout_ms=context.settle_timeout_ms,
        quiet_frames=context.quiet_frames,
        read=read,
        release=release,
    )
    if pool.scan.capped:
        await release(pool)
        report = HealAttemptReport(
            rung=2,
            attempt=request.attempt,
            outcome=RungOutcome.CANDIDATE_CAP_REACHED,
            on_page=pool.scan.total,
        )
        return Rung2Result(report)
    ranked = await _ranked(context, request, pool)
    decision = decide(
        ranked, threshold=config.accept_threshold, required_margin=config.accept_margin
    )
    confirmed: ElementIdentity | None = None
    if isinstance(decision, Accepted):
        confirmed = await browser.identify(decision.winner.candidate.element, confirm=True)
        if confirmed.confirmed is False:
            refused = decision.winner.rejected(
                SafetyRejection(
                    reason=RejectionReason.UNCONFIRMED_IDENTITY,
                    detail="Playwright could not confirm the role and name computed for it",
                )
            )
            ranked = (refused, *ranked[1:])
            decision = Declined(RungOutcome.TOP_REJECTED, decision.runner_up, decision.margin)
    report = _report(context, request, pool.scan.total, ranked, decision, confirmed)
    if (
        context.chooser is not None
        and isinstance(decision, Declined)
        and takes_up(decision.outcome)
    ):
        return Rung2Result(report, declined=Rung2Decline(ranked, report, epoch))
    winner = decision.winner.candidate.element if isinstance(decision, Accepted) else None
    await browser.release(
        [item.candidate.element for item in ranked if item.candidate.element != winner]
    )
    if isinstance(decision, Accepted) and confirmed is not None:
        accepted = AcceptedHeal(rung=2, scored=decision.winner, identity=confirmed, report=report)
        return Rung2Result(report, accepted)
    return Rung2Result(report)


async def _seeded(browser: BrowserPort, seeds: Sequence[Seed]) -> tuple[Pooled, ...]:
    """The elements earlier rungs found, re-read in this snapshot."""
    found: list[Pooled] = []
    for selector, origin in seeds:
        match = await browser.resolve_unique(selector)
        if match.element is None:
            continue
        identity = await browser.identify(match.element, confirm=False)
        try:
            facts = await browser.element_facts(match.element)
        except TargetNotFound:
            await browser.release([match.element])
            continue
        found.append((LiveCandidate(element=match.element, identity=identity, facts=facts), origin))
    return tuple(found)


async def _ranked(
    context: LadderContext, request: ClimbRequest, pool: _Pool
) -> tuple[ScoredElement, ...]:
    browser = context.browser
    config = context.config
    candidates, dropped = await _distinct(browser, pool, request.step.action)
    scored: list[ScoredElement] = []
    for candidate, origin in candidates:
        rejection = safety_rejection(
            request.step, request.fingerprint, candidate, config.vocabulary
        )
        item = score_candidate(request.fingerprint, candidate, origin, config, rejection)
        if item.signature in request.excluded:
            dropped.append(candidate.element)
        else:
            scored.append(item)
    await browser.release(dropped)
    return rank(scored)


async def _distinct(
    browser: BrowserPort, pool: _Pool, action: ActionType
) -> tuple[list[Pooled], list[ElementRef]]:
    """Seeds and action-compatible scanned elements, each DOM node once, seeds first."""
    dropped: list[ElementRef] = []
    pooled: list[Pooled] = list(pool.seeded)
    for candidate in pool.scan.candidates:
        if compatible(action, candidate):
            pooled.append((candidate, CandidateOrigin.PAGE))
        else:
            dropped.append(candidate.element)
    if not pooled:
        return [], dropped
    keys = await browser.group_identical([candidate.element for candidate, _ in pooled])
    kept: list[Pooled] = []
    for position, (item, key) in enumerate(zip(pooled, keys, strict=True)):
        if key == position:
            kept.append(item)
        else:
            dropped.append(item[0].element)
    return kept, dropped


def _report(
    context: LadderContext,
    request: ClimbRequest,
    on_page: int,
    ranked: tuple[ScoredElement, ...],
    decision: Accepted | Declined,
    confirmed: ElementIdentity | None,
) -> HealAttemptReport:
    config = context.config
    shown = ranked[: config.report_candidates]
    candidates = tuple(
        scored_candidate(item, f"c{index + 1}", context.scrubber, confirmed if index == 0 else None)
        for index, item in enumerate(shown)
    )
    runner = decision.runner_up
    runner_id = next((f"c{index + 1}" for index, item in enumerate(ranked) if item is runner), None)
    top = ranked[0] if ranked else None
    kind_change = (
        compare_kinds(recorded_kind(request.fingerprint), found_kind(top.candidate)).change
        if top is not None
        else None
    )
    accepted = isinstance(decision, Accepted)
    outcome = RungOutcome.RESOLVED if isinstance(decision, Accepted) else decision.outcome
    return HealAttemptReport(
        rung=2,
        attempt=request.attempt,
        outcome=outcome,
        candidates=candidates,
        considered=len(ranked),
        on_page=on_page,
        chosen="c1" if accepted else None,
        runner_up=runner_id,
        score=top.score if top is not None else None,
        margin=decision.margin,
        threshold=config.accept_threshold,
        required_margin=config.accept_margin,
        kind_change=kind_change,
    )
