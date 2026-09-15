"""What a run report shows, decided from a run's record and the version it executed (ADR 0013).

A pure view model: every renderer (the static HTML report now, a dashboard later) draws the same
facts. It never reads a field's value: steps are described by intent, checks, and where a value came
from. Checkpoint strength is shown per step, so a run that passed only on ``url_matches`` is visibly
weaker than its pass rate suggests (ADR 0010).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from mendwork.engine.domain.approvals import ProposalRecord
from mendwork.engine.domain.enums import ActionType, VerificationStrength
from mendwork.engine.domain.heals import HealAttemptReport, ScoredCandidate
from mendwork.engine.domain.model_evidence import CandidateDescription, ModelUsageTotals
from mendwork.engine.domain.patches import CaptureProblem, ImageBox
from mendwork.engine.domain.runs import ArtifactName, Run, StepResult, StepStatus
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.diff import TargetDiff, target_diff
from mendwork.engine.patching.history import step_strengths, weak_summary
from mendwork.engine.patching.words import (
    checkpoint_words,
    element_kind,
    moment,
    quoted,
    score_words,
    strength_sentence,
    target_words,
)
from mendwork.engine.reporting.words import (
    PROPOSAL_OUTCOME_WORDS,
    RUN_STATUS_WORDS,
    RUNG_OUTCOME_WORDS,
    STEP_STATUS_WORDS,
    VERIFICATION_WORDS,
    duration_words,
    patch_outcome_words,
    run_patch_lines,
    source_words,
    usage_words,
)
from mendwork.engine.safety.heal_policy import verification_strength

_MODEL_RUNG: Final = 3
_TONES: Final = {
    StepStatus.SUCCEEDED: "ok",
    StepStatus.FAILED: "bad",
    StepStatus.CANCELLED: "bad",
    StepStatus.AWAITING_APPROVAL: "stop",
    StepStatus.NEEDS_REVIEW: "stop",
    StepStatus.NOT_RUN: "idle",
}


@dataclass(frozen=True, slots=True)
class CheckView:
    """One checkpoint of a step, and what it found."""

    words: str
    passed: bool
    detail: str | None


@dataclass(frozen=True, slots=True)
class RungView:
    """What one rung of the heal ladder did in one attempt."""

    heading: str
    numbers: str | None
    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelView:
    """What a model was shown and answered for a step, and what that cost."""

    shown: tuple[str, ...]
    answer: str
    reason: str | None
    usage: str


@dataclass(frozen=True, slots=True)
class FoundView:
    """The healed element next to the recorded one."""

    screenshot: ArtifactName | None
    box: ImageBox | None
    recorded: str
    found: str | None
    diff: TargetDiff | None
    problem: str | None


@dataclass(frozen=True, slots=True)
class ApprovalView:
    """A proposal a step made, and what a person decided."""

    proposal_id: str
    decision: str
    outcome: str | None


@dataclass(frozen=True, slots=True)
class StepView:
    """One step of the run."""

    number: int
    step_id: str
    action: str
    intent: str
    status: str
    tone: str
    """ok, bad, stop, or idle: how the report colours the step."""
    duration: str
    share: float
    """The step's duration as a fraction of the longest step's, for the timeline."""
    resolution: str
    healed: bool
    stopped: bool
    strength: VerificationStrength
    strength_words: str
    checks: tuple[CheckView, ...]
    screenshot: ArtifactName | None
    found: FoundView | None
    rungs: tuple[RungView, ...]
    model: ModelView | None
    approvals: tuple[ApprovalView, ...]
    patches: tuple[str, ...]
    error: str | None
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunReportView:
    """Everything a run report shows."""

    title: str
    run_id: str
    workflow_id: str
    version: int
    status: str
    tone: str
    started: str
    duration: str
    steps_line: str
    usage: str
    source: str | None
    strength_summary: str | None
    patches: tuple[str, ...]
    error: str | None
    steps: tuple[StepView, ...]

    def images(self) -> tuple[ArtifactName, ...]:
        """Every screenshot, most telling first: found elements, the stopping step, healed steps,
        then every other step in order."""
        found = [
            step.found.screenshot
            for step in self.steps
            if step.found is not None and step.found.screenshot is not None
        ]
        stopped = [step.screenshot for step in self.steps if step.stopped and step.screenshot]
        healed = [step.screenshot for step in self.steps if step.healed and step.screenshot]
        rest = [step.screenshot for step in self.steps if step.screenshot]
        return tuple(dict.fromkeys([*found, *stopped, *healed, *rest]))


def run_report_view(run: Run, workflow: WorkflowVersion) -> RunReportView:
    """The report of a run, from its record and the exact version it executed."""
    longest = max((step.duration_ms or 0 for step in run.steps), default=0)
    strengths = step_strengths(workflow)
    succeeded = sum(1 for step in run.steps if step.status is StepStatus.SUCCEEDED)
    steps = tuple(
        _step_view(run, step, result, longest)
        for step, result in zip(workflow.steps, run.steps, strict=False)
    )
    tone = (
        "ok"
        if run.status.value == "succeeded"
        else "stop"
        if "review" in run.status.value
        else "bad"
    )
    return RunReportView(
        title=f"{run.workflow_id} v{run.workflow_version} · run {run.run_id}",
        run_id=run.run_id,
        workflow_id=run.workflow_id,
        version=run.workflow_version,
        status=RUN_STATUS_WORDS[run.status],
        tone="stop" if run.status.value == "awaiting_approval" else tone,
        started=moment(run.started_at),
        duration=duration_words(run.duration_ms),
        steps_line=f"{succeeded} of {len(run.steps)} steps succeeded",
        usage=usage_words(run.model_usage),
        source=(
            source_words(run.source, run.workflow_id, run.workflow_version)
            if run.source is not None
            else None
        ),
        strength_summary=weak_summary(strengths),
        patches=run_patch_lines(run),
        error=f"{run.error.type}: {run.error.message}" if run.error is not None else None,
        steps=steps,
    )


def _step_view(run: Run, step: Step, result: StepResult, longest: int) -> StepView:
    heal = result.heal
    strength = verification_strength(step.checkpoints)
    kinds = [item.kind for item in step.checkpoints]
    return StepView(
        number=result.index + 1,
        step_id=result.step_id,
        action=result.action.value,
        intent=step.intent,
        status=STEP_STATUS_WORDS[result.status],
        tone=_TONES[result.status],
        duration=duration_words(result.duration_ms),
        share=(result.duration_ms or 0) / longest if longest else 0.0,
        resolution=_resolution(step, result),
        healed=heal is not None and heal.healed_rung is not None,
        stopped=result.status not in {StepStatus.SUCCEEDED, StepStatus.NOT_RUN},
        strength=strength,
        strength_words=strength_sentence(strength, kinds),
        checks=tuple(
            CheckView(
                words=(
                    checkpoint_words(step.checkpoints[item.index])
                    if item.index < len(step.checkpoints)
                    else item.kind.value
                ),
                passed=item.passed,
                detail=item.detail or item.reason,
            )
            for item in sorted(result.checkpoints, key=lambda item: item.index)
        ),
        screenshot=result.artifacts.screenshot,
        found=_found(step, result),
        rungs=tuple(_rung(report) for report in heal.attempts) if heal is not None else (),
        model=_model(heal.attempts) if heal is not None else None,
        approvals=tuple(
            _approval(item) for item in run.proposals if item.proposal.step_id == result.step_id
        ),
        patches=tuple(
            patch_outcome_words(item, run.workflow_id)
            for item in run.patches
            if item.step_id == result.step_id
        ),
        error=f"{result.error.type}: {result.error.message}" if result.error is not None else None,
        evidence=_evidence(result),
    )


def _resolution(step: Step, result: StepResult) -> str:
    heal = result.heal
    target = result.target
    if result.status is StepStatus.NOT_RUN:
        return "Not run"
    if heal is not None and heal.healed_rung is not None:
        chooser = ", chosen by an AI model" if heal.healed_rung == _MODEL_RUNG else ""
        return f"Healed at rung {heal.healed_rung}{chooser}"
    if heal is not None and heal.proposal is not None:
        return f"A heal at rung {heal.proposal.rung} waits for a person's approval"
    if heal is not None and heal.abstention is not None:
        return f"Abstained ({heal.abstention.value.replace('_', ' ')}): nothing was acted on"
    if target is not None and target.pending_patch is not None:
        return f"Found by a pending patch's target (patch {target.pending_patch})"
    if target is not None and target.resolved_rank is not None:
        return (
            f"Found by its recorded selector {target.resolved_rank + 1} of {len(target.selectors)}"
        )
    if step.action is ActionType.NAVIGATE:
        return "Loaded the page" if result.navigation is not None else "The page did not load"
    if step_target(step) is None:
        return "Acted on the page without a target"
    return "The recorded element was not found"


def _found(step: Step, result: StepResult) -> FoundView | None:
    found = result.found
    recorded = step_target(step)
    if found is None or recorded is None:
        return None
    fingerprint = found.fingerprint
    return FoundView(
        screenshot=found.screenshot,
        box=found.box,
        recorded=target_words(recorded),
        found=target_words(fingerprint) if fingerprint is not None else None,
        diff=target_diff(recorded, fingerprint) if fingerprint is not None else None,
        problem=_problem(found.problem),
    )


def _problem(problem: CaptureProblem | None) -> str | None:
    if problem is None:
        return None
    return (
        f"The healed element could not be fingerprinted ({problem.value.replace('_', ' ')}), "
        "so this heal cannot become a version."
    )


def _rung(report: HealAttemptReport) -> RungView:
    heading = (
        f"Attempt {report.attempt}, rung {report.rung}: {RUNG_OUTCOME_WORDS[report.outcome]}; "
        f"{VERIFICATION_WORDS[report.verification]}"
    )
    if report.kind_change is not None:
        heading += f" (the element changed kind: {report.kind_change})"
    return RungView(
        heading=heading,
        numbers=score_words(report.score, report.threshold, report.margin, report.required_margin),
        candidates=tuple(_candidate(item) for item in report.candidates),
    )


def _candidate(candidate: ScoredCandidate) -> str:
    identity = candidate.identity
    kind = element_kind(identity.role, identity.tag, identity.input_type)
    words = f"{kind} named {quoted(identity.name)}, similarity {candidate.score:.2f}"
    if candidate.rejection is not None:
        words += f", refused: {candidate.rejection.detail}"
    return words


def _model(attempts: Sequence[HealAttemptReport]) -> ModelView | None:
    evidence = next((report.model for report in reversed(attempts) if report.model), None)
    if evidence is None:
        return None
    totals = ModelUsageTotals()
    for call in evidence.calls:
        totals = totals.plus(call.usage)
    confidence = f" with confidence {evidence.confidence:.2f}" if evidence.confidence else ""
    answer = (
        f"It chose line {evidence.choice}{confidence}."
        if evidence.choice is not None
        else "It chose none of them."
    )
    return ModelView(
        shown=tuple(
            f"{item.number}. {_description(item.description)} (similarity {item.similarity:.2f})"
            for item in evidence.shown
        ),
        answer=answer,
        reason=evidence.reason,
        usage=usage_words(totals),
    )


def _description(description: CandidateDescription) -> str:
    words = f"{description.kind} {quoted(description.name or '')}"
    if description.label:
        words += f", labelled {quoted(description.label)}"
    if description.nearby_text:
        words += ", near " + ", ".join(quoted(text) for text in description.nearby_text)
    return words


def _approval(item: ProposalRecord) -> ApprovalView:
    decision = item.decision
    decided = (
        f"{decision.kind.value} at {moment(decision.at)} (audit entry {decision.audit_sequence})"
        if decision is not None
        else "waiting for a person's decision"
    )
    if decision is not None and decision.reason:
        decided += f": {decision.reason}"
    outcome = PROPOSAL_OUTCOME_WORDS[item.outcome] if item.outcome is not None else None
    if outcome is not None and item.detail:
        outcome += f": {item.detail}"
    return ApprovalView(proposal_id=item.proposal.id, decision=decided, outcome=outcome)


def _evidence(result: StepResult) -> tuple[str, ...]:
    artifacts = result.artifacts
    items = [
        f"{label}: {name}"
        for label, name in (
            ("DOM snapshot", artifacts.dom_snapshot),
            ("Trace", artifacts.trace),
            ("Download", artifacts.download),
        )
        if name is not None
    ]
    if artifacts.trace_withheld is not None:
        items.append(
            "Trace withheld: the page could hold a value typed from a secret "
            f"({artifacts.trace_withheld.reason.value.replace('_', ' ')})"
        )
    items.extend(f"Not captured: {problem}" for problem in artifacts.capture_errors)
    return tuple(items)
