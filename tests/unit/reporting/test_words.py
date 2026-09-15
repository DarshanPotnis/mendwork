"""The words a report and the CLI share for sources, patch outcomes, and model usage (ADR 0013)."""

from decimal import Decimal

import pytest

from mendwork.engine.domain.model_evidence import ModelUsageTotals
from mendwork.engine.domain.patches import (
    NotSavedReason,
    PatchOutcome,
    PatchResult,
    WorkflowSource,
)
from mendwork.engine.reporting.words import patch_outcome_words, source_words, usage_words


def outcome(result: PatchResult, **fields: object) -> str:
    return patch_outcome_words(
        PatchOutcome.model_validate({"step_id": "export", "result": result, **fields}), "ledger"
    )


def test_a_run_that_saves_heals_says_which_version_ran() -> None:
    saved = WorkflowSource(path="a.yaml", stored_version=1, ran_stored=True, saves_heals=True)

    assert source_words(saved, "ledger", 3) == (
        "Ran ledger v3 from the workflow store; the file given is v1. "
        "Heals from this run are saved."
    )


@pytest.mark.parametrize(
    ("reason", "start"),
    [
        (NotSavedReason.EXACT, "Ran the file exactly as written (--exact)"),
        (NotSavedReason.FILE_DIFFERS, "The file differs from every stored version of ledger"),
        (NotSavedReason.NO_LINEAGE, "The workflow store has no versions of ledger"),
        (NotSavedReason.NEWER_IMPORT, "The file matches ledger v1, but a later version"),
        (NotSavedReason.STORE_UNAVAILABLE, "The workflow store could not be read"),
    ],
)
def test_a_run_that_saves_no_heals_says_why(reason: NotSavedReason, start: str) -> None:
    source = WorkflowSource(stored_version=1, saves_heals=False, not_saved=reason)

    assert source_words(source, "ledger", 1).startswith(start)


def test_every_patch_outcome_reads_plainly() -> None:
    assert outcome(PatchResult.PENDING, successes=1, required=3) == (
        "verified in 1 of 3 runs; saved as a version once 3 runs verify it"
    )
    assert outcome(PatchResult.ALREADY_APPLIED, version=2) == "already saved in ledger v2"
    assert outcome(PatchResult.PREVIOUSLY_ROLLED_BACK, version=3).startswith(
        "not saved again: v3 rolled back this exact heal."
    )
    assert outcome(PatchResult.DISCARDED, detail="the step changed") == (
        "pending patch removed: the step changed"
    )
    assert outcome(PatchResult.CONFLICT) == "not saved: conflict"
    assert outcome(PatchResult.PUBLISHED, version=2, rung=1) == "saved as ledger v2 (rung 1)"


@pytest.mark.parametrize(
    ("usage", "words"),
    [
        (ModelUsageTotals(), "No model calls."),
        (
            ModelUsageTotals(calls=1, input_tokens=300, output_tokens=30),
            "1 model call, 330 tokens, about $0.0000.",
        ),
        (
            ModelUsageTotals(calls=1, unreported_token_calls=1),
            "1 model call, token counts not reported by this provider, about $0.0000.",
        ),
        (
            ModelUsageTotals(calls=3, input_tokens=5, output_tokens=5, unreported_token_calls=2),
            "3 model calls, 10 tokens for 1 of 3 calls (token counts not reported for 2), about "
            "$0.0000.",
        ),
        (
            ModelUsageTotals(
                calls=2, input_tokens=5, output_tokens=5, estimated_cost_usd=Decimal("0.0123")
            ),
            "2 model calls, 10 tokens, about $0.0123.",
        ),
        (
            ModelUsageTotals(calls=2, input_tokens=5, output_tokens=5, unpriced_calls=2),
            "2 model calls, 10 tokens, cost unknown (no price in MENDWORK_MODEL_PRICES).",
        ),
        (
            ModelUsageTotals(
                calls=3,
                input_tokens=5,
                output_tokens=5,
                estimated_cost_usd=Decimal("0.0123"),
                unpriced_calls=1,
            ),
            "3 model calls, 10 tokens, about $0.0123 for 2 of 3 calls (cost unknown for 1, no "
            "price in MENDWORK_MODEL_PRICES).",
        ),
    ],
    ids=[
        "no calls",
        "tokens reported",
        "tokens not reported",
        "tokens reported for some calls",
        "all priced",
        "none priced",
        "some priced",
    ],
)
def test_model_usage_never_shows_unreported_tokens_as_0_or_unpriced_calls_as_free(
    usage: ModelUsageTotals, words: str
) -> None:
    assert usage_words(usage) == words
