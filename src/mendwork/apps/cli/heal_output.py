"""Readable heal output: what each rung examined, why a heal was accepted, and what to do next.

Presentation only. Every fact shown comes from heal events and error reports. An abstention
is a correct outcome, so its output explains what was compared and ends with a concrete next
step chosen by the reason nothing was acted on. Rung 3's lines live in ``model_output``.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue

from mendwork.apps.cli.heal_words import INDENT, describe
from mendwork.apps.cli.model_output import model_next_step, rung3_lines
from mendwork.engine.domain.events import HealVerifiedEvent, StateRestoredEvent
from mendwork.engine.domain.heals import (
    AbstentionReason,
    HealAttemptReport,
    RungOutcome,
    ScoredCandidate,
)
from mendwork.engine.domain.runs import ErrorReport
from mendwork.engine.domain.targets import IdentityReport, TargetEvidence

DIFFERENCE_WORDS: Final = {
    "role": "role",
    "tag": "tag",
    "input_type": "type",
    "accessible_name": "accessible name",
    "unconfirmed": "identity not confirmed by Playwright",
}
_FEATURE_LABELS: Final = (
    ("name", "name"),
    ("label", "label"),
    ("attributes", "attributes"),
    ("role", "role"),
    ("tag_type", "tag/type"),
    ("nearby_text", "nearby"),
    ("structural_path", "path"),
    ("position", "position"),
)
_CHECKPOINTS_THAT_PROVE: Final = (
    "url_matches, element_visible, text_present, download_completed, or response_received"
)
_MODEL_RUNG: Final = 3


def attempt_lines(report: HealAttemptReport) -> list[str]:
    """What one rung examined and concluded."""
    match report.rung:
        case 0:
            return [INDENT + _rung0(report)]
        case 1:
            return [INDENT + _rung1(report)]
        case 2:
            return _rung2(report)
        case 3:
            return rung3_lines(report)


def verified_lines(event: HealVerifiedEvent) -> list[str]:
    """Whether the checkpoints proved a heal."""
    if event.passed:
        if event.rung == _MODEL_RUNG:
            return [
                f"{INDENT}HEALED at rung 3 (model choice): every checkpoint passed after acting on "
                "the model's choice; the checkpoints, not the model, decided"
            ]
        return [
            f"{INDENT}HEALED at rung {event.rung}: every checkpoint passed after acting on the "
            "healed target"
        ]
    failed = event.failed_checkpoint
    which = (
        f"checkpoint {failed.index + 1} ({failed.kind})" if failed is not None else "a checkpoint"
    )
    return [f"{INDENT}heal not verified: {which} failed, so that candidate is excluded"]


def restored_lines(event: StateRestoredEvent) -> list[str]:
    """How the page was restored after a heal failed verification."""
    recovery = event.recovery
    if not recovery.restored:
        return [f"{INDENT}could not restore the page: {recovery.reason}"]
    replayed = f" and replayed {', '.join(recovery.replayed)}" if recovery.replayed else ""
    cleared = "; cleared the field the failed attempt typed into" if recovery.cleared_field else ""
    return [f"{INDENT}restored the page: re-opened {recovery.url}{replayed}{cleared}"]


def resolution_line(evidence: TargetEvidence) -> str | None:
    """The target line for a healed step, or None for a Rung 0 resolution."""
    if evidence.healed_rung is None:
        return None
    rung = (
        "rung 3 (model choice)"
        if evidence.healed_rung == _MODEL_RUNG
        else f"rung {evidence.healed_rung}"
    )
    return f"target: healed at {rung} → {describe(evidence.identity)}"


def stop_headline(error: ErrorReport) -> str:
    """The first line of a step that did not succeed."""
    match error.type:
        case "HealAbstained":
            return f"ABSTAINED: {error.message}"
        case "ApprovalRequired":
            return f"AWAITING APPROVAL: {error.message}"
        case "NeedsReview":
            return f"NEEDS REVIEW: {error.message}"
        case "EgressBlocked":
            return f"BLOCKED BY EGRESS POLICY: {error.message}"
        case "RunCancelled":
            return f"INTERRUPTED: {error.message}"
        case "ApprovalStale":
            return f"APPROVAL STALE: {error.message}"
        case "ProposalRejected":
            return f"REJECTED: {error.message}"
        case _:
            return f"FAILED {error.type}: {error.message}"


def next_step(error: ErrorReport) -> str | None:
    """What a person should do after the ladder stopped, chosen by why it stopped."""
    context = error.context
    match error.type:
        case "ApprovalRequired":
            proposal = context.get("proposal_id")
            named = f"proposal {proposal}" if isinstance(proposal, str) else "its heal"
            return (
                f"Next: this step is irreversible, so {named} needs a person's approval before "
                "anything acts on it. Review it with `mendwork show`, then approve or reject it; "
                "the exact commands follow the summary."
            )
        case "ApprovalStale":
            return (
                "Next: the page changed after the proposal was approved, so nothing acted on it. "
                "Run the workflow again; if the step still needs a heal, it makes a fresh proposal."
            )
        case "ProposalRejected":
            return "Next: re-record this step, or fix the page it runs on, then run the workflow."
        case "NeedsReview":
            return (
                "Next: check in the application what the irreversible action did before running "
                "this workflow again; Mendwork will not retry it."
            )
        case "HealAbstained":
            return _abstention_step(str(context.get("reason")), context)
        case "EgressBlocked":
            return egress_next_step(context)
        case "RunCancelled":
            return _interrupted_step(context)
        case _:
            return None


def _interrupted_step(context: Mapping[str, JsonValue]) -> str:
    steps = context.get("irreversible_steps")
    if isinstance(steps, list) and steps:
        named = ", ".join(str(step) for step in steps)
        return (
            f"Next: check in the application what step {named} did before running this workflow "
            "again; Mendwork never re-runs a run that needs review."
        )
    return "Next: nothing irreversible was sent; run the workflow again when you are ready."


def egress_next_step(context: Mapping[str, JsonValue]) -> str:
    """What to do after the egress policy refused a navigation or a connection, by its rule."""
    host = context.get("host") or "that destination"
    match context.get("rule"):
        case "not_allowlisted":
            return (
                f"Next: if this workflow should automate {host}, add it to "
                "MENDWORK_EGRESS_ALLOWED_DOMAINS; otherwise find out why the run was led there."
            )
        case "blocked_address" if context.get("address_range") == "loopback":
            return (
                "Next: runs never reach loopback addresses. If this is a local test target, name "
                "its exact ip:port in MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS (refused in production)."
            )
        case "blocked_address":
            return (
                f"Next: {host} is an internal or metadata address, and no setting lets a run reach "
                "one. Point the workflow at the site's public address."
            )
        case _:
            return (
                "Next: the run was led to a URL Mendwork never loads (another scheme, embedded "
                "credentials, or an unreadable address). Fix the navigate step's URL, or find out "
                "why the page redirected there."
            )


def _abstention_step(reason: str, context: Mapping[str, JsonValue]) -> str:
    model_step = model_next_step(reason, context)
    if model_step is not None:
        return model_step
    if reason == AbstentionReason.CANDIDATE_CAP_REACHED:
        return (
            f"Next: raise MENDWORK_HEAL_CANDIDATES_MAX above {context.get('on_page')} (it is "
            f"{context.get('candidates_max')}) to let healing run on this page, or re-record "
            "this step."
        )
    if reason == AbstentionReason.TOP_REJECTED:
        return (
            "Next: look at the page. The control most like the recorded one was refused by a "
            f"safety rule ({str(context.get('rejection', 'safety')).replace('_', ' ')}); if the "
            "change is intended, re-record this step. Safety rules are never relaxed to get "
            "past it."
        )
    steps: dict[str, str] = {
        AbstentionReason.NO_CANDIDATES: (
            "Next: the control this step needs is not on the page. Check the page by hand; if it "
            "moved to another page or was replaced, re-record this step."
        ),
        AbstentionReason.BELOW_THRESHOLD: (
            "Next: nothing on the page matches the recording closely enough. If one of the "
            "candidates above is the right control, re-record this step on the current page. "
            "Do not lower MENDWORK_HEAL_ACCEPT_THRESHOLD to get past one page: it applies to "
            "every heal."
        ),
        AbstentionReason.BELOW_MARGIN: (
            "Next: several controls match about equally well, so choosing would be a guess. "
            "Re-record this step on the current page; the recorder scopes a repeated control to "
            "its row or section."
        ),
        AbstentionReason.PAGE_NEVER_STABLE: (
            "Next: the page kept changing while it was examined. Re-run once it has finished "
            "loading, or re-record the step against the page in a stable state."
        ),
        AbstentionReason.UNVERIFIABLE: (
            "Next: add a checkpoint that proves what this step does "
            f"({_CHECKPOINTS_THAT_PROVE}), then re-run, or re-record the step."
        ),
        AbstentionReason.AUTHENTICATION_LIMIT: (
            "Next: sign in by hand to check the account is not locked, then re-record this step. "
            "Sign-in steps get one heal attempt per run."
        ),
        AbstentionReason.ATTEMPTS_EXHAUSTED: (
            "Next: every heal Mendwork tried failed this step's checkpoints. Re-record this step "
            "on the current page."
        ),
        AbstentionReason.RESTORE_FAILED: (
            "Next: re-run the workflow from the start; if it stops here again, re-record this step."
        ),
        AbstentionReason.HEAL_TIMED_OUT: (
            "Next: re-run; if the page is slow, raise MENDWORK_HEAL_TIMEOUT_MS, or re-record "
            "this step."
        ),
    }
    return steps.get(
        reason,
        "Next: re-run the workflow; if it stops here again, re-record this step.",
    )


def _rung0(report: HealAttemptReport) -> str:
    target = report.target
    match report.outcome:
        case RungOutcome.DRIFTED:
            identity = target.identity if target is not None else None
            changed = _differences(target)
            return (
                f"rung 0: the recorded selectors agree on {describe(identity)}, but its {changed} "
                "differs from the recording"
            )
        case RungOutcome.AMBIGUOUS:
            return "rung 0: the recorded selectors do not point at one element"
        case _:
            return "rung 0: no recorded selector found a visible element"


def _rung1(report: HealAttemptReport) -> str:
    target = report.target
    count = len(target.selectors) if target is not None else 0
    selectors = f"{count} alternate selector{'' if count == 1 else 's'}"
    match report.outcome:
        case RungOutcome.RESOLVED:
            return (
                f"rung 1: {selectors} found the recorded {describe(_identity(report))} "
                f"(score {report.score or 0:.2f}, needs {report.threshold or 0:.2f})"
            )
        case RungOutcome.DRIFTED:
            identity = target.identity if target is not None else None
            changed = _differences(target)
            return (
                f"rung 1: {selectors} agree on {describe(identity)}, but its {changed} differs; "
                "it is compared at rung 2"
            )
        case RungOutcome.AMBIGUOUS:
            return f"rung 1: {selectors} do not point at one element"
        case RungOutcome.BELOW_THRESHOLD | RungOutcome.TOP_REJECTED:
            return (
                f"rung 1: {selectors} found the recorded identity, but it "
                f"{_rung1_refusal(report)}; it is compared at rung 2"
            )
        case RungOutcome.PAGE_NEVER_STABLE:
            return "rung 1: the page kept changing while it was examined"
        case _:
            if count == 0:
                return "rung 1: the fingerprint holds no selector the recording did not already try"
            return f"rung 1: {selectors} found nothing"


def _rung2(report: HealAttemptReport) -> list[str]:
    if report.outcome is RungOutcome.CANDIDATE_CAP_REACHED:
        return [
            f"{INDENT}rung 2: the page has {report.on_page} candidates, more than the configured "
            "limit, so healing does not run on it"
        ]
    if report.outcome is RungOutcome.PAGE_NEVER_STABLE:
        return [f"{INDENT}rung 2: the page kept changing while candidates were compared"]
    considered = report.considered
    lines = [
        f"{INDENT}rung 2: compared {considered} candidate{'' if considered == 1 else 's'} "
        f"({report.on_page} on the page)"
    ]
    if report.outcome is RungOutcome.RESOLVED and report.candidates:
        chosen = report.candidates[0]
        runner = next((c for c in report.candidates if c.id == report.runner_up), None)
        over = f" over {describe(runner.identity)}" if runner is not None else ""
        lines.append(
            f"{INDENT}  chose {describe(chosen.identity)} · score {chosen.score:.2f} "
            f"(needs {report.threshold or 0:.2f}) · margin {report.margin or 0:.2f}{over} "
            f"(needs {report.required_margin or 0:.2f})"
        )
        lines.append(f"{INDENT}  {_breakdown(chosen)}")
        if report.kind_change is not None:
            lines.append(
                f"{INDENT}  kind changed {report.kind_change}: allowed because this step checks "
                "the effect of activating it"
            )
        return lines
    for position, candidate in enumerate(report.candidates, start=1):
        refused = (
            f"  REFUSED: {candidate.rejection.detail}" if candidate.rejection is not None else ""
        )
        lines.append(
            f"{INDENT}  {position}. {describe(candidate.identity)} {candidate.score:.2f}{refused}"
        )
    return lines


def _breakdown(candidate: ScoredCandidate) -> str:
    features = candidate.features
    return " · ".join(f"{label} {getattr(features, field):.2f}" for field, label in _FEATURE_LABELS)


def _identity(report: HealAttemptReport) -> IdentityReport | None:
    return report.candidates[0].identity if report.candidates else None


def _rung1_refusal(report: HealAttemptReport) -> str:
    candidate = report.candidates[0] if report.candidates else None
    if candidate is not None and candidate.rejection is not None:
        return f"was refused: {candidate.rejection.detail}"
    return f"scored {report.score or 0:.2f}, below {report.threshold or 0:.2f}"


def _differences(target: TargetEvidence | None) -> str:
    if target is None or not target.differences:
        return "identity"
    return ", ".join(DIFFERENCE_WORDS.get(item, item) for item in target.differences)
