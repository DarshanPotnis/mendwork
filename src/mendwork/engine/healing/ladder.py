"""The heal ladder: Rung 0's failure in, an accepted heal or an abstention out.

Each rung is tried only when the one below could not safely proceed, and every rung reports
what it examined. Rung 3 (a model choosing among Rung 2's candidates) arrives in Phase 6;
until then, when Rung 2 cannot accept, the ladder abstains with full evidence.

Only three Rung 0 outcomes are healed: nothing found, several found, and a drifted identity.
A page that never stops changing is not healed, because nothing on it can be compared safely;
an element that changed between verification and action is not either, because the action
was about to reach a verified element.
"""

from dataclasses import dataclass
from typing import Final

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import (
    AbstentionReason,
    CandidateOrigin,
    HealAttemptReport,
    RungOutcome,
)
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import (
    AmbiguousTarget,
    MendworkError,
    PageNeverStable,
    TargetDrifted,
    TargetNotFound,
)
from mendwork.engine.healing.context import AcceptedHeal, ClimbRequest, LadderContext
from mendwork.engine.healing.rung1 import run_rung1
from mendwork.engine.healing.rung2 import Seed, run_rung2
from mendwork.engine.replay.reports import target_evidence

HEALABLE_DRIFT: Final = "identity_changed"
_ABSTENTIONS: Final = {
    RungOutcome.NO_CANDIDATES: AbstentionReason.NO_CANDIDATES,
    RungOutcome.BELOW_THRESHOLD: AbstentionReason.BELOW_THRESHOLD,
    RungOutcome.BELOW_MARGIN: AbstentionReason.BELOW_MARGIN,
    RungOutcome.TOP_REJECTED: AbstentionReason.TOP_REJECTED,
    RungOutcome.CANDIDATE_CAP_REACHED: AbstentionReason.CANDIDATE_CAP_REACHED,
}


@dataclass(frozen=True, slots=True)
class ClimbResult:
    """Every rung's report in order, and either an accepted heal or why there is none."""

    reports: tuple[HealAttemptReport, ...]
    accepted: AcceptedHeal | None = None
    abstention: AbstentionReason | None = None


def is_healable(error: MendworkError) -> bool:
    """Whether a Rung 0 failure goes to the ladder rather than ending the step."""
    if isinstance(error, TargetNotFound | AmbiguousTarget):
        return error.context.get("reason") not in {"detached_before_action"}
    if isinstance(error, TargetDrifted):
        return error.context.get("reason") == HEALABLE_DRIFT
    return False


def rung0_report(failure: MendworkError, attempt: int) -> HealAttemptReport:
    """Rung 0's failure as the ladder's first report."""
    if isinstance(failure, TargetDrifted):
        outcome = RungOutcome.DRIFTED
    elif isinstance(failure, AmbiguousTarget):
        outcome = RungOutcome.AMBIGUOUS
    else:
        outcome = RungOutcome.NOT_FOUND
    return HealAttemptReport(
        rung=0, attempt=attempt, outcome=outcome, target=target_evidence(failure)
    )


async def climb(
    context: LadderContext, request: ClimbRequest, failure: MendworkError
) -> ClimbResult:
    """Rungs 1 and 2 after a Rung 0 failure."""
    reports = [rung0_report(failure, request.attempt)]
    seeds: list[Seed] = []
    drifted = _drifted_selector(request.fingerprint, reports[0])
    if drifted is not None:
        seeds.append((drifted, CandidateOrigin.RUNG0_DRIFTED))
    try:
        rung1 = await run_rung1(context, request)
        reports.append(rung1.report)
        if rung1.accepted is not None:
            return ClimbResult(tuple(reports), accepted=rung1.accepted)
        if rung1.seed is not None:
            seeds.append((rung1.seed, CandidateOrigin.RUNG1))
        rung2 = await run_rung2(context, request, seeds)
    except PageNeverStable:
        reports.append(
            HealAttemptReport(
                rung=1 if len(reports) == 1 else 2,
                attempt=request.attempt,
                outcome=RungOutcome.PAGE_NEVER_STABLE,
            )
        )
        return ClimbResult(tuple(reports), abstention=AbstentionReason.PAGE_NEVER_STABLE)
    reports.append(rung2.report)
    if rung2.accepted is not None:
        return ClimbResult(tuple(reports), accepted=rung2.accepted)
    return ClimbResult(tuple(reports), abstention=_ABSTENTIONS[rung2.report.outcome])


def _drifted_selector(fingerprint: Fingerprint, report: HealAttemptReport) -> Selector | None:
    target = report.target
    if report.outcome is not RungOutcome.DRIFTED or target is None or target.resolved_rank is None:
        return None
    return fingerprint.selectors[target.resolved_rank]
