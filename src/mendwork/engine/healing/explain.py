"""Abstentions in words: what the ladder looked at and why nothing was acted on.

The message says what happened; the structured context carries every number behind it, so
output layers can show the evidence and suggest what a person should do next. An abstention
Rung 3 decided also says what the model was shown and answered, or why it was not asked.
"""

from pydantic import JsonValue

from mendwork.engine.domain.heals import (
    AbstentionReason,
    HealAttemptReport,
    RungOutcome,
    ScoredCandidate,
)
from mendwork.engine.domain.model_evidence import ModelCallOutcome
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
            if report.rung == 3:
                return "the page changed while the model was choosing, so its choice was not used"
            return "the page kept changing while candidates were compared"
        case (
            AbstentionReason.UNVERIFIABLE
            | AbstentionReason.AUTHENTICATION_LIMIT
            | AbstentionReason.ATTEMPTS_EXHAUSTED
            | AbstentionReason.RESTORE_FAILED
            | AbstentionReason.HEAL_TIMED_OUT
        ):
            if report.rung == 3 and report.outcome is RungOutcome.NOT_ASKED:
                return _not_asked(reason)
            return f"the heal ladder abstained ({reason.value})"
        case (
            AbstentionReason.MODEL_ABSTAINED
            | AbstentionReason.MODEL_CHOICE_OUT_OF_RANGE
            | AbstentionReason.MODEL_OUTPUT_INVALID
            | AbstentionReason.MODEL_UNAVAILABLE
            | AbstentionReason.MODEL_BUDGET_EXHAUSTED
            | AbstentionReason.MODEL_CHOICE_REFUSED
        ):
            return _model_message(reason, report)


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
    refused = _refused(report)
    if refused is not None and refused.rejection is not None:
        context["rejection"] = refused.rejection.reason.value
    evidence = report.model
    if evidence is None:
        return context
    context["model_shown"] = len(evidence.shown)
    context["model_calls"] = len(evidence.calls)
    if evidence.calls:
        usage = evidence.calls[-1].usage
        context["model_provider"] = usage.provider
        context["model_name"] = usage.model
    if evidence.choice is not None:
        context["model_choice"] = evidence.choice
    if evidence.reason is not None:
        context["model_reason"] = evidence.reason
    problem = _last_problem(report)
    if problem is not None:
        context["model_problem"] = problem
    if evidence.unavailable is not None:
        context["model_unavailable"] = evidence.unavailable
    if evidence.budget is not None:
        context["budget_scope"] = evidence.budget.scope.value
        context["budget_limit"] = evidence.budget.limit
        if evidence.budget.resets_at is not None:
            context["budget_resets_at"] = evidence.budget.resets_at.isoformat()
    return context


def _not_asked(reason: AbstentionReason) -> str:
    match reason:
        case AbstentionReason.UNVERIFIABLE:
            return (
                "the scoring could not decide, and a model was not asked because this step has no "
                "checkpoint that could prove its choice"
            )
        case AbstentionReason.AUTHENTICATION_LIMIT:
            return (
                "the scoring could not decide, and a model was not asked because this sign-in "
                "step has used its one heal attempt"
            )
        case _:
            return (
                "the scoring could not decide, and a model was not asked because this step has "
                "used every heal attempt it may make"
            )


def _model_message(reason: AbstentionReason, report: HealAttemptReport) -> str:
    evidence = report.model
    shown = len(evidence.shown) if evidence is not None else 0
    listed = f"{shown} candidate{'' if shown == 1 else 's'}"
    match reason:
        case AbstentionReason.MODEL_ABSTAINED:
            return (
                "neither the scoring nor the model identified the recorded control: the model was "
                f"shown {listed} and answered that none of them clearly is"
            )
        case AbstentionReason.MODEL_CHOICE_OUT_OF_RANGE:
            choice = evidence.choice if evidence is not None else None
            return (
                f"the model answered {choice}, which is not one of the {listed} it was shown, so "
                "nothing was chosen"
            )
        case AbstentionReason.MODEL_OUTPUT_INVALID:
            return (
                "the model twice replied in a form that could not be used "
                f"({_last_problem(report) or 'no usable reply'})"
            )
        case AbstentionReason.MODEL_UNAVAILABLE:
            detail = evidence.unavailable if evidence is not None else None
            return f"the model could not be asked: {detail or 'the provider did not answer'}"
        case AbstentionReason.MODEL_BUDGET_EXHAUSTED:
            budget = evidence.budget if evidence is not None else None
            return f"no model call was made: {budget.detail if budget else 'the budget is used up'}"
        case _:
            refused = _refused(report)
            if refused is None or refused.rejection is None:
                return "the model's choice was refused by a safety rule"
            identity = refused.identity
            return (
                f'the model chose {identity.role or identity.tag} "{identity.name}", which was '
                f"refused: {refused.rejection.detail}"
            )


def _refused(report: HealAttemptReport) -> ScoredCandidate | None:
    return next(
        (candidate for candidate in report.candidates if candidate.rejection is not None), None
    )


def _last_problem(report: HealAttemptReport) -> str | None:
    evidence = report.model
    if evidence is None:
        return None
    problems = [
        call.problem
        for call in evidence.calls
        if call.outcome is ModelCallOutcome.INVALID_OUTPUT and call.problem is not None
    ]
    return problems[-1] if problems else None
