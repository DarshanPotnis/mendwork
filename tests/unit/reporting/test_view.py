"""What a run report shows, decided from the run's record and its version (ADR 0013)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from mendwork.engine.domain.approvals import (
    DecisionKind,
    ProposalDecision,
    ProposalOutcome,
    ProposalRecord,
)
from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.model_evidence import (
    CandidateDescription,
    ModelCall,
    ModelCallOutcome,
    ModelCallPurpose,
    ModelChoiceEvidence,
    ModelUsage,
    ShownCandidate,
)
from mendwork.engine.domain.patches import (
    CaptureProblem,
    FoundTarget,
    ImageBox,
)
from mendwork.engine.domain.runs import (
    ArtifactName,
    Run,
    RunStatus,
    StepArtifacts,
    TraceWithheld,
    TraceWithheldReason,
)
from mendwork.engine.reporting.view import run_report_view
from tests.unit.patching.builders import (
    RECORDED_NAME,
    ledger,
    ledger_page,
    ledger_version,
    result,
)
from tests.unit.replay.approval_builders import proposal

pytestmark = pytest.mark.asyncio


async def healed() -> Run:
    made = await ledger()
    return await made.run(ledger_page(), ledger_version(), patcher=made.patcher())


async def test_a_healed_run_shows_the_heal_its_numbers_its_strength_and_the_new_version() -> None:
    run = await healed()

    view = run_report_view(run, ledger_version())

    assert (view.status, view.tone, view.steps_line, view.usage) == (
        "Succeeded",
        "ok",
        "2 of 2 steps succeeded",
        "No model calls.",
    )
    assert view.patches == ("Step 2 export: saved as ledger v2 (rung 2, verified strongly)",)
    assert view.source == "Ran ledger v1; heals from this run are saved to the workflow store."
    assert view.strength_summary is not None
    opened, exported = view.steps
    assert (opened.resolution, opened.strength, opened.checks) == (
        "Loaded the page",
        VerificationStrength.NONE,
        (),
    )
    assert (exported.resolution, exported.healed, exported.strength) == (
        "Healed at rung 2",
        True,
        VerificationStrength.STRONG,
    )
    assert exported.strength_words == "That check is strong: it shows the right element was used."
    [check] = exported.checks
    assert (check.words, check.passed) == ('the text "Ledger exported" appeared', True)
    found = exported.found
    assert found is not None
    assert (found.screenshot, found.box) == (
        ArtifactName("steps/002_export.found.png"),
        ImageBox(x=0.4, y=0.3, width=0.1, height=0.05),
    )
    assert found.recorded == f'a button named "{RECORDED_NAME}"'
    assert found.found == 'a button named "Share ledger"'
    assert found.diff is not None
    assert [(item.label, item.changed) for item in found.diff.fields][:2] == [
        ("Kind", False),
        ("Name", True),
    ]
    assert [rung.heading.split(":")[0] for rung in exported.rungs] == [
        "Attempt 1, rung 0",
        "Attempt 1, rung 1",
        "Attempt 1, rung 2",
    ]
    assert exported.rungs[-1].heading.endswith("verified by the step's checks")
    assert exported.patches == ("saved as ledger v2 (rung 2, verified strongly)",)
    assert view.images() == (
        ArtifactName("steps/002_export.found.png"),
        ArtifactName("steps/002_export.png"),
        ArtifactName("steps/001_open.png"),
    )


async def test_a_run_that_stopped_shows_its_error_its_evidence_and_the_steps_not_run() -> None:
    made = await ledger()
    page = ledger_page(RECORDED_NAME, exports=False)
    run = await made.run(page, ledger_version())
    step = result(run, "export").model_copy(
        update={
            "artifacts": StepArtifacts(
                screenshot=ArtifactName("steps/002_export.png"),
                dom_snapshot=ArtifactName("failure/002_export.dom.html"),
                trace_withheld=TraceWithheld(reason=TraceWithheldReason.SECRET_BEARING_PAGE),
                capture_errors=("trace: BrowserUnavailable: gone",),
            )
        }
    )
    run = run.model_copy(update={"steps": (run.steps[0], step)})

    view = run_report_view(run, ledger_version())

    assert (view.status, view.tone) == ("Failed", "bad")
    assert view.error is not None
    exported = view.steps[1]
    assert (exported.status, exported.tone, exported.stopped) == ("Failed", "bad", True)
    assert exported.error is not None
    assert exported.error.startswith("CheckpointFailed: ")
    assert exported.resolution == "Found by its recorded selector 1 of 1"
    assert exported.evidence == (
        "DOM snapshot: failure/002_export.dom.html",
        "Trace withheld: the page could hold a value typed from a secret (secret bearing page)",
        "Not captured: trace: BrowserUnavailable: gone",
    )
    assert [check.passed for check in exported.checks] == [False]
    assert view.images()[0] == ArtifactName("steps/002_export.png")


async def test_a_paused_step_shows_its_proposal_and_a_decided_one_its_outcome() -> None:
    run = await healed()
    decided = ProposalDecision(
        kind=DecisionKind.APPROVED, at=datetime(2026, 9, 15, 18, 5, tzinfo=UTC), audit_sequence=4
    )
    run = run.model_copy(
        update={
            "status": RunStatus.AWAITING_APPROVAL,
            "proposals": (
                ProposalRecord(
                    proposal=proposal(),
                    decision=decided,
                    outcome=ProposalOutcome.STALE,
                    detail="another element",
                ),
                ProposalRecord(proposal=proposal(number=2)),
            ),
        }
    )

    view = run_report_view(run, ledger_version())

    assert (view.status, view.tone) == ("Awaiting approval", "stop")
    assert [
        (item.proposal_id, item.decision, item.outcome) for item in view.steps[1].approvals
    ] == [
        (
            "export-1",
            "approved at 15 Sep 2026 18:05 UTC (audit entry 4)",
            "stale, so nothing was acted on: another element",
        ),
        ("export-2", "waiting for a person's decision", None),
    ]


@pytest.mark.parametrize(
    ("tokens", "usage"),
    [
        ((400, 60), "1 model call, 460 tokens, about $0.0000."),
        ((None, None), "1 model call, token counts not reported by this provider, about $0.0000."),
    ],
    ids=["reported", "not reported"],
)
async def test_a_model_s_list_answer_reason_and_cost_are_shown(
    tokens: tuple[int | None, int | None], usage: str
) -> None:
    run = await healed()
    step = result(run, "export")
    assert step.heal is not None
    evidence = ModelChoiceEvidence(
        prompt_version="choose-candidate/1",
        shown=(
            ShownCandidate(
                number=1,
                candidate="c1",
                description=CandidateDescription(
                    kind="button", name="Share ledger", label="Ledger", nearby_text=("Quarterly",)
                ),
                similarity=0.51,
            ),
        ),
        calls=(
            ModelCall(
                purpose=ModelCallPurpose.CHOOSE,
                outcome=ModelCallOutcome.ANSWERED,
                usage=ModelUsage(
                    provider="ollama",
                    model="qwen3",
                    input_tokens=tokens[0],
                    output_tokens=tokens[1],
                    latency_ms=3000,
                    http_attempts=1,
                    estimated_cost_usd=Decimal(0),
                ),
            ),
        ),
        choice=1,
        confidence=0.9,
        reason="the same control",
    )
    last = step.heal.attempts[-1].model_copy(update={"rung": 3, "model": evidence})
    heal = step.heal.model_copy(update={"attempts": (*step.heal.attempts[:-1], last)})
    changed = step.model_copy(update={"heal": heal})

    view = run_report_view(
        run.model_copy(update={"steps": (run.steps[0], changed)}), ledger_version()
    )

    model = view.steps[1].model
    assert model is not None
    assert model.shown == (
        '1. button "Share ledger", labelled "Ledger", near "Quarterly" (similarity 0.51)',
    )
    assert (model.answer, model.reason, model.usage) == (
        "It chose line 1 with confidence 0.90.",
        "the same control",
        usage,
    )


async def test_a_heal_whose_element_could_not_be_fingerprinted_says_so() -> None:
    run = await healed()
    step = result(run, "export").model_copy(
        update={"found": FoundTarget(problem=CaptureProblem.NO_SELECTOR)}
    )

    view = run_report_view(run.model_copy(update={"steps": (run.steps[0], step)}), ledger_version())

    found = view.steps[1].found
    assert found is not None
    assert (found.found, found.diff, found.screenshot) == (None, None, None)
    assert found.problem == (
        "The healed element could not be fingerprinted (no selector), so this heal cannot become "
        "a version."
    )
