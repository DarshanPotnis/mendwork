"""Abstentions in words: what the ladder looked at and why nothing was acted on.

The message says what happened; the structured context carries every number behind it, so
output layers can show the evidence and suggest what a person should do next.
"""

from pydantic import JsonValue

from mendwork.engine.domain.heals import AbstentionReason, HealAttemptReport
from mendwork.engine.errors import HealAbstained
from mendwork.engine.healing.config import HealingConfig


def abstention_message(
    reason: AbstentionReason, report: HealAttemptReport, config: HealingConfig
) -> str:
    """Why the deciding rung acted on nothing."""
    score = report.score if report.score is not None else 0.0
    margin = report.margin if report.margin is not None else 0.0
    match reason:
        case AbstentionReason.NO_CANDIDATES:
            return "no element on the page could receive this step's action"
        case AbstentionReason.BELOW_THRESHOLD:
            return (
                f"the closest candidate scored {score:.2f}, below the accept threshold of "
                f"{config.accept_threshold:.2f}"
            )
        case AbstentionReason.BELOW_MARGIN:
            return (
                f"the closest candidate scored {score:.2f} but led the next by only {margin:.2f}, "
                f"less than the required margin of {config.accept_margin:.2f}, so choosing would "
                "be a guess"
            )
        case AbstentionReason.TOP_REJECTED:
            rejection = report.candidates[0].rejection if report.candidates else None
            detail = rejection.detail if rejection is not None else "a safety rule refused it"
            return f"the element most like the recorded one was refused: {detail}"
        case AbstentionReason.CANDIDATE_CAP_REACHED:
            return (
                f"the page has {report.on_page} elements this action could receive, more than the "
                f"limit of {config.candidates_max}, so healing does not run on it"
            )
        case AbstentionReason.PAGE_NEVER_STABLE:
            return "the page kept changing while candidates were compared"
        case (
            AbstentionReason.UNVERIFIABLE
            | AbstentionReason.AUTHENTICATION_LIMIT
            | AbstentionReason.ATTEMPTS_EXHAUSTED
            | AbstentionReason.RESTORE_FAILED
            | AbstentionReason.HEAL_TIMED_OUT
        ):
            return f"the heal ladder abstained ({reason.value})"


def abstained(
    reason: AbstentionReason, report: HealAttemptReport, config: HealingConfig
) -> HealAbstained:
    """The abstention as an error: its message, with every number behind it as context."""
    return HealAbstained(
        abstention_message(reason, report, config),
        **abstention_context(reason, report, config),
    )


def abstention_context(
    reason: AbstentionReason, report: HealAttemptReport, config: HealingConfig
) -> dict[str, JsonValue]:
    """The numbers behind an abstention, for records and output."""
    context: dict[str, JsonValue] = {
        "reason": reason.value,
        "rung": report.rung,
        "on_page": report.on_page,
        "considered": report.considered,
        "score": report.score,
        "margin": report.margin,
        "threshold": config.accept_threshold,
        "required_margin": config.accept_margin,
        "candidates_max": config.candidates_max,
    }
    rejection = report.candidates[0].rejection if report.candidates else None
    if rejection is not None:
        context["rejection"] = rejection.reason.value
    return context
