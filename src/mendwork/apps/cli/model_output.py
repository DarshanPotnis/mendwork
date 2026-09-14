"""Rung 3 in readable output: what the model was shown, what it chose and why, what that cost,
and what a person should do when it could not help.

Presentation only. Every fact comes from heal events, error reports, and run records. A model's
choice is always shown as a proposal the safety rules and checkpoints decided on.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Final

from pydantic import JsonValue

from mendwork.apps.cli.heal_words import INDENT
from mendwork.engine.domain.heals import (
    AbstentionReason,
    HealAttemptReport,
    RejectionReason,
    RungOutcome,
)
from mendwork.engine.domain.model_evidence import (
    BudgetScope,
    ModelCall,
    ModelCallOutcome,
    ModelChoiceEvidence,
    ModelUsageTotals,
    ShownCandidate,
)

_CENT: Final = Decimal("0.01")


def rung3_lines(report: HealAttemptReport) -> list[str]:
    """What Rung 3 showed the model, what it answered, what it cost, and what came of it."""
    not_asked = _not_asked(report.outcome)
    if not_asked is not None:
        return [f"{INDENT}rung 3: {not_asked}"]
    evidence = report.model
    if evidence is None:
        return [f"{INDENT}rung 3: {report.outcome.value}"]
    if not evidence.calls and evidence.budget is not None:
        return [f"{INDENT}rung 3: the model was not asked: {evidence.budget.detail}"]
    lines = [INDENT + _asked(evidence)]
    lines.extend(
        f"{INDENT}  reply could not be used: {call.problem}" for call in _invalid(evidence)
    )
    if evidence.unavailable is not None:
        lines.append(f"{INDENT}  the model could not answer: {evidence.unavailable}")
    if evidence.budget is not None:
        lines.append(f"{INDENT}  no further call was made: {evidence.budget.detail}")
    if evidence.reason is not None:
        lines.append(f"{INDENT}  {_answer(evidence)}")
    lines.append(f"{INDENT}  {usage_summary(evidence.calls)}")
    verdict = _verdict(report)
    if verdict is not None:
        lines.append(f"{INDENT}  {verdict}")
    return lines


def _not_asked(outcome: RungOutcome) -> str | None:
    """Why the model was never asked, for outcomes that say so on their own; None otherwise."""
    match outcome:
        case RungOutcome.NO_ELIGIBLE:
            return (
                "no candidate both passed the safety rules and shared wording or identity "
                "attributes with the recording, so the model was not asked"
            )
        case RungOutcome.LOOK_ALIKES:
            return (
                "the closest candidates read exactly alike, so a model could only guess between "
                "them and was not asked"
            )
        case RungOutcome.NOT_ASKED:
            return "the model was not asked, because this step cannot act on a heal"
        case _:
            return None


def usage_summary(calls: Sequence[ModelCall]) -> str:
    """Calls, tokens, time, and estimated cost, in one line."""
    totals = ModelUsageTotals()
    for call in calls:
        totals = totals.plus(call.usage)
    return _totals_text(totals)


def model_usage_line(totals: ModelUsageTotals) -> str | None:
    """The run's model usage for the summary, or None when no model call was made."""
    if totals.calls == 0:
        return None
    return f"Model: {_totals_text(totals)}"


def model_next_step(reason: str, context: Mapping[str, JsonValue]) -> str | None:
    """What a person should do after Rung 3 abstained, or None for another reason."""
    name = context.get("model_name") or "the configured model"
    match reason:
        case AbstentionReason.MODEL_ABSTAINED:
            return (
                "Next: neither the scoring nor the model could tell which control is the recorded "
                "one. If one of the candidates above is right, re-record this step on the current "
                "page."
            )
        case AbstentionReason.MODEL_CHOICE_OUT_OF_RANGE:
            return (
                "Next: the model answered a number that was not on the list, which counts as no "
                "answer. Re-run; if it happens again, set MENDWORK_MODEL_NAME to another model, "
                "or re-record this step."
            )
        case AbstentionReason.MODEL_OUTPUT_INVALID:
            problem = context.get("model_problem") or "no usable reply"
            return (
                f"Next: the model twice replied in a form Mendwork cannot use ({problem}). Check "
                f"that {name} supports JSON-schema output, or re-record this step."
            )
        case AbstentionReason.MODEL_UNAVAILABLE:
            return _unavailable_step(context, str(name))
        case AbstentionReason.MODEL_BUDGET_EXHAUSTED:
            return _budget_step(context)
        case AbstentionReason.MODEL_CHOICE_REFUSED if context.get("rejection") == (
            RejectionReason.WEAK_VERIFICATION
        ):
            return (
                "Next: this step's checkpoints only check where it leads or what the field holds, "
                "so a model's choice must keep the recorded id, name, or test id. Add a checkpoint "
                "that observes the step's own effect (element_visible or text_present) to the "
                "workflow, or re-record this step."
            )
        case AbstentionReason.MODEL_CHOICE_REFUSED:
            rule = str(context.get("rejection", "safety")).replace("_", " ")
            return (
                "Next: look at the page. The model's choice was refused by a safety rule "
                f"({rule}); if the change is intended, re-record this step. Safety rules are "
                "never relaxed to get past it."
            )
        case _:
            return None


def _asked(evidence: ModelChoiceEvidence) -> str:
    usage = evidence.calls[0].usage if evidence.calls else None
    who = f"{usage.provider} {usage.model}" if usage is not None else "the model"
    shown = len(evidence.shown)
    others = evidence.ineligible + evidence.not_shown
    left_out = (
        f" ({others} other candidate{'' if others == 1 else 's'} not shown)" if others else ""
    )
    return (
        f"rung 3: asked {who} to choose among {shown} candidate{'' if shown == 1 else 's'}"
        f"{left_out}"
    )


def _answer(evidence: ModelChoiceEvidence) -> str:
    choice = evidence.choice
    if choice is None:
        chosen = "none of them"
    else:
        shown = next((item for item in evidence.shown if item.number == choice), None)
        chosen = _shown(shown) if shown is not None else f"{choice}, which is not on the list"
    confidence = (
        f" · confidence {evidence.confidence:.2f}" if evidence.confidence is not None else ""
    )
    return f'model chose {chosen}{confidence} · "{evidence.reason}"'


def _shown(item: ShownCandidate) -> str:
    name = item.description.name
    return (
        f'{item.number}. {item.description.kind} "{name}"'
        if name
        else f"{item.number}. {item.description.kind}"
    )


def _verdict(report: HealAttemptReport) -> str | None:
    match report.outcome:
        case RungOutcome.RESOLVED:
            return (
                "its choice passed every safety rule and Playwright confirmed it; the step's "
                "checkpoints decide whether it was right"
            )
        case RungOutcome.CHOICE_REFUSED:
            refused = next((c for c in report.candidates if c.rejection is not None), None)
            detail = refused.rejection.detail if refused is not None and refused.rejection else ""
            return f"REFUSED: {detail}" if detail else "REFUSED by a safety rule"
        case RungOutcome.PAGE_NEVER_STABLE:
            return "the page changed while the model was choosing, so its choice was not used"
        case RungOutcome.CHOICE_OUT_OF_RANGE:
            return "that number is not on the list, which counts as no answer"
        case _:
            return None


def _invalid(evidence: ModelChoiceEvidence) -> list[ModelCall]:
    return [call for call in evidence.calls if call.outcome is ModelCallOutcome.INVALID_OUTPUT]


def _totals_text(totals: ModelUsageTotals) -> str:
    calls = f"{totals.calls} call{'' if totals.calls == 1 else 's'}"
    tokens = f"{totals.input_tokens} tokens in, {totals.output_tokens} out"
    seconds = f"{totals.latency_ms / 1000:.2f} s"
    return f"{calls} · {tokens} · {seconds} · {_cost(totals)}"


def _cost(totals: ModelUsageTotals) -> str:
    priced = totals.calls - totals.unpriced_calls
    unknown = (
        f"cost unknown for {totals.unpriced_calls} call{'' if totals.unpriced_calls == 1 else 's'} "
        "(no price in MENDWORK_MODEL_PRICES)"
    )
    if priced == 0:
        return unknown
    amount = totals.estimated_cost_usd
    shown = f"est. ${amount:.2f}" if amount == 0 or amount >= _CENT else f"est. ${amount:.6f}"
    return shown if totals.unpriced_calls == 0 else f"{shown}; {unknown}"


def _unavailable_step(context: Mapping[str, JsonValue], name: str) -> str:
    said = context.get("model_unavailable") or "no answer"
    if context.get("model_provider") == "ollama":
        return (
            f"Next: start Ollama (`ollama serve`) and check that `ollama list` shows {name}, then "
            f"re-run. It said: {said}."
        )
    return (
        "Next: check MENDWORK_MODEL_BASE_URL, MENDWORK_MODEL_API_KEY, and MENDWORK_MODEL_NAME, "
        f"then re-run. The provider said: {said}."
    )


def _budget_step(context: Mapping[str, JsonValue]) -> str:
    limit = context.get("budget_limit")
    if context.get("budget_scope") == BudgetScope.RUN:
        return (
            f"Next: this run used all {limit} model calls MENDWORK_MODEL_MAX_CALLS_PER_RUN allows. "
            "Re-record the steps that needed healing; raise the limit only if this many model "
            "choices in one run is expected."
        )
    resets = context.get("budget_resets_at")
    if resets is None:
        return (
            "Next: the model usage ledger (under MENDWORK_ARTIFACTS_DIR/usage) could not be read, "
            "so no call was made. Fix or remove the file it names, then re-run."
        )
    return (
        f"Next: today's {limit} model calls (MENDWORK_MODEL_MAX_CALLS_PER_DAY) are used up; the "
        f"count starts again at {resets}. Re-record this step, or raise the limit if the spend "
        "is expected."
    )
