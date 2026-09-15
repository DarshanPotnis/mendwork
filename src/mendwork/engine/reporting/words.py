"""Words the run report and the CLI share: statuses, sources, patch outcomes, and approvals.

Kept in the engine so a report, a terminal, and later a dashboard describe the same run in the same
words. Every fact comes from a run record; nothing here decides anything.
"""

from collections.abc import Mapping
from typing import Final

from mendwork.engine.domain.approvals import ProposalOutcome
from mendwork.engine.domain.heals import RungOutcome, Verification
from mendwork.engine.domain.model_evidence import ModelUsageTotals
from mendwork.engine.domain.patches import NotSavedReason, PatchOutcome, PatchResult, WorkflowSource
from mendwork.engine.domain.runs import Run, RunStatus, StepStatus
from mendwork.engine.patching.words import cost_words, strength_adverb, token_words

RUN_STATUS_WORDS: Final[Mapping[RunStatus, str]] = {
    RunStatus.RUNNING: "Running",
    RunStatus.SUCCEEDED: "Succeeded",
    RunStatus.FAILED: "Failed",
    RunStatus.CANCELLED: "Cancelled",
    RunStatus.AWAITING_APPROVAL: "Awaiting approval",
    RunStatus.NEEDS_REVIEW: "Needs review",
}
STEP_STATUS_WORDS: Final[Mapping[StepStatus, str]] = {
    StepStatus.SUCCEEDED: "Succeeded",
    StepStatus.FAILED: "Failed",
    StepStatus.CANCELLED: "Cancelled",
    StepStatus.AWAITING_APPROVAL: "Awaiting approval",
    StepStatus.NEEDS_REVIEW: "Needs review",
    StepStatus.NOT_RUN: "Not run",
}
PROPOSAL_OUTCOME_WORDS: Final[Mapping[ProposalOutcome, str]] = {
    ProposalOutcome.ACTED_VERIFIED: "acted on the approved element, and its checkpoints passed",
    ProposalOutcome.ACTED_UNVERIFIED: "acted on, but the step did not pass; check what it did",
    ProposalOutcome.NOT_NEEDED: "not needed: the recorded selectors found the recorded element",
    ProposalOutcome.STALE: "stale, so nothing was acted on",
    ProposalOutcome.INTERRUPTED: "interrupted before the approved step finished",
    ProposalOutcome.NOT_RESUMED: "approved, but the run stopped before it resumed",
}
RUNG_OUTCOME_WORDS: Final[Mapping[RungOutcome, str]] = {
    RungOutcome.RESOLVED: "found the element",
    RungOutcome.DRIFTED: "found an element whose identity changed",
    RungOutcome.AMBIGUOUS: "found several elements",
    RungOutcome.NOT_FOUND: "found nothing",
    RungOutcome.NO_CANDIDATES: "had nothing to compare",
    RungOutcome.BELOW_THRESHOLD: "found no element close enough",
    RungOutcome.BELOW_MARGIN: "found elements too close to tell apart",
    RungOutcome.TOP_REJECTED: "refused the closest element",
    RungOutcome.CANDIDATE_CAP_REACHED: "found too many elements to compare",
    RungOutcome.PAGE_NEVER_STABLE: "saw the page keep changing",
    RungOutcome.NO_ELIGIBLE: "had no element a model could be shown",
    RungOutcome.LOOK_ALIKES: "found look-alike elements, so no model was asked",
    RungOutcome.NOT_ASKED: "asked no model",
    RungOutcome.MODEL_ABSTAINED: "asked a model, which chose none",
    RungOutcome.CHOICE_OUT_OF_RANGE: "asked a model, which answered outside the list",
    RungOutcome.OUTPUT_INVALID: "asked a model, whose reply could not be used",
    RungOutcome.MODEL_UNAVAILABLE: "could not ask the model",
    RungOutcome.BUDGET_EXHAUSTED: "found the model budget used up",
    RungOutcome.CHOICE_REFUSED: "refused the model's choice",
}
VERIFICATION_WORDS: Final[Mapping[Verification, str]] = {
    Verification.NOT_PERFORMED: "nothing was acted on",
    Verification.PENDING: "about to act",
    Verification.PASSED: "verified by the step's checks",
    Verification.FAILED: "failed the step's checks",
}


def source_words(source: WorkflowSource, workflow_id: str, version: int) -> str:
    """Where the version a run executed came from, and whether its heals are saved."""
    stored = source.stored_version
    if source.saves_heals:
        if source.ran_stored and stored is not None:
            return (
                f"Ran {workflow_id} v{version} from the workflow store; the file given is "
                f"v{stored}. Heals from this run are saved."
            )
        return f"Ran {workflow_id} v{version}; heals from this run are saved to the workflow store."
    match source.not_saved:
        case NotSavedReason.EXACT:
            return "Ran the file exactly as written (--exact); heals from this run are not saved."
        case NotSavedReason.FILE_DIFFERS:
            return (
                f"The file differs from every stored version of {workflow_id}, so heals from this "
                "run are not saved."
            )
        case NotSavedReason.NO_LINEAGE:
            return (
                f"The workflow store has no versions of {workflow_id} and this file is not a first "
                "version, so heals from this run are not saved."
            )
        case NotSavedReason.NEWER_IMPORT:
            return (
                f"The file matches {workflow_id} v{stored}, but a later version was imported, so "
                "the file ran as written and heals from this run are not saved."
            )
        case NotSavedReason.STORE_UNAVAILABLE | None:
            return (
                "The workflow store could not be read, so the file ran as written and heals from "
                "this run are not saved."
            )


def patch_outcome_words(outcome: PatchOutcome, workflow_id: str) -> str:
    """What came of one heal or pending patch."""
    detail = outcome.detail or outcome.result.value.replace("_", " ")
    match outcome.result:
        case PatchResult.PUBLISHED:
            strength = f", verified {strength_adverb(outcome.strength)}" if outcome.strength else ""
            return f"saved as {workflow_id} v{outcome.version} (rung {outcome.rung}{strength})"
        case PatchResult.PENDING:
            return (
                f"verified in {outcome.successes} of {outcome.required} runs; saved as a version "
                f"once {outcome.required} runs verify it"
            )
        case PatchResult.ALREADY_APPLIED:
            return f"already saved in {workflow_id} v{outcome.version}"
        case PatchResult.PREVIOUSLY_ROLLED_BACK:
            return (
                f"not saved again: v{outcome.version} rolled back this exact heal. If it is right "
                f"after all, restore the version that had it with mendwork rollback {workflow_id} "
                "--to <version>"
            )
        case PatchResult.DISCARDED:
            return f"pending patch removed: {detail}"
        case _:
            return f"not saved: {detail}"


def run_patch_lines(run: Run) -> tuple[str, ...]:
    """What came of each of a run's heals and pending patches, one line each, naming its step."""
    numbers = {item.step_id: item.index + 1 for item in run.steps}
    return tuple(
        f"Step {numbers.get(item.step_id, 0)} {item.step_id}: "
        f"{patch_outcome_words(item, run.workflow_id)}"
        for item in run.patches
    )


def usage_words(usage: ModelUsageTotals) -> str:
    """What a run's model calls cost, in one sentence."""
    if usage.calls == 0:
        return "No model calls."
    calls = "call" if usage.calls == 1 else "calls"
    tokens = token_words(
        usage.input_tokens + usage.output_tokens, usage.calls, usage.unreported_token_calls
    )
    cost = cost_words(usage.estimated_cost_usd, usage.calls, usage.unpriced_calls, prefix="about ")
    return f"{usage.calls} model {calls}, {tokens}, {cost}."


def duration_words(milliseconds: int | None) -> str:
    """A duration as seconds with two decimals."""
    return f"{(milliseconds or 0) / 1000:.2f}s"
