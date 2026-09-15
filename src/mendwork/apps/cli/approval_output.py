"""Readable output for approvals: a run's proposals, and what a decision did.

Presentation only: every fact shown comes from the run's record and its saved workflow.
"""

from pathlib import Path
from typing import Final

from mendwork.apps.cli.heal_words import INDENT
from mendwork.apps.cli.human_output import render_summary
from mendwork.engine.domain.approvals import ProposalOutcome, ProposalRecord
from mendwork.engine.domain.heals import HealProposal
from mendwork.engine.domain.runs import ArtifactName, Run, RunStatus
from mendwork.engine.domain.steps import step_target
from mendwork.engine.domain.targets import IdentityReport
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.safety.approvals import find_proposal

_OUTCOME_WORDS: Final = {
    ProposalOutcome.ACTED_VERIFIED: "acted on the approved element, and its checkpoints passed",
    ProposalOutcome.ACTED_UNVERIFIED: "acted on, but the step did not pass; check what it did",
    ProposalOutcome.NOT_NEEDED: "not needed: the recorded selectors found the recorded element",
    ProposalOutcome.STALE: "stale, so nothing was acted on",
    ProposalOutcome.INTERRUPTED: "interrupted before the approved step finished",
    ProposalOutcome.NOT_RESUMED: "approved, but the run stopped before it resumed",
}


def render_show(run: Run, workflow: WorkflowVersion | None, runs_directory: Path) -> str:
    """A run as it stands: its header, its steps, every proposal, and each segment's egress."""
    status = run.status.value.replace("_", " ").upper()
    lines = [
        f"Run {run.run_id} · {run.workflow_id} v{run.workflow_version} · {status}",
        f"Artifacts: {runs_directory / run.run_id}",
        render_summary(run, runs_directory),
    ]
    for item in run.proposals:
        lines.extend(["", *proposal_lines(run, item, workflow, runs_directory)])
    lines.extend(["", *egress_lines(run)])
    return "\n".join(lines)


def proposal_lines(
    run: Run, item: ProposalRecord, workflow: WorkflowVersion | None, runs_directory: Path
) -> list[str]:
    """One proposal: the recorded and found elements, the numbers, the evidence, the decision."""
    proposal = item.proposal
    state = item.decision.kind.value if item.decision is not None else "pending"
    lines = [
        f"Proposal {proposal.id} · step {proposal.step_index + 1} {proposal.step_id} · {state}"
    ]
    recorded = _recorded(workflow, proposal.step_index)
    if recorded is not None:
        lines.append(f"{INDENT}recorded    {recorded}")
    lines.append(
        f"{INDENT}found       {_describe(proposal.candidate.identity)} · rung {proposal.rung}"
    )
    lines.append(f"{INDENT}numbers     {_numbers(proposal)}")
    box = proposal.box
    if box is not None:
        lines.append(
            f"{INDENT}position    x {box.x:.2f} · y {box.y:.2f} · {box.width:.2f} by "
            f"{box.height:.2f} of the page (shown, never matched)"
        )
    model = proposal.model
    if model is not None and model.choice is not None:
        reason = f": {model.reason}" if model.reason else ""
        lines.append(f"{INDENT}model       chose candidate {model.choice}{reason}")
    screenshot = _paused_screenshot(run, proposal)
    if screenshot is not None:
        uri = (runs_directory / run.run_id / screenshot).resolve().as_uri()
        lines.append(f"{INDENT}screenshot  {uri}")
    decision = item.decision
    if decision is not None:
        reason = f": {decision.reason}" if decision.reason else ""
        lines.append(
            f"{INDENT}decision    {decision.kind.value} at {decision.at:%Y-%m-%d %H:%M:%S} UTC "
            f"(audit entry {decision.audit_sequence}){reason}"
        )
    words = outcome_words(item)
    if words is not None:
        lines.append(f"{INDENT}outcome     {words}")
    return lines


def outcome_words(item: ProposalRecord) -> str | None:
    """What came of an approval, in words, with the detail that explains it."""
    if item.outcome is None:
        return None
    words = _OUTCOME_WORDS[item.outcome]
    return f"{words}: {item.detail}" if item.detail else words


def outcome_line(run: Run, proposal_id: str) -> str | None:
    """The line that ends ``mendwork approve``: what came of the approval."""
    item = find_proposal(run, proposal_id)
    words = outcome_words(item) if item is not None else None
    return f"Proposal {proposal_id}: {words}" if words is not None else None


def rejected_lines(run: Run, proposal_id: str) -> list[str]:
    """What ``mendwork reject`` reports once the rejection is recorded."""
    item = find_proposal(run, proposal_id)
    decision = item.decision if item is not None else None
    entry = f" (audit entry {decision.audit_sequence})" if decision is not None else ""
    lines = [f"Rejected proposal {proposal_id} of run {run.run_id}{entry}."]
    if decision is not None and decision.reason:
        lines.append(f"Reason: {decision.reason}")
    lines.append(
        "The run is failed and nothing was acted on. Re-record the step, or fix the page it runs "
        "on, then run the workflow."
    )
    return lines


def egress_lines(run: Run) -> list[str]:
    """The egress policy each segment of the run was held to."""
    lines: list[str] = []
    for number, segment in enumerate(run.segments, start=1):
        allowed = ", ".join(segment.egress_allowed_domains) or "no domains"
        loopback = segment.egress_loopback_exceptions
        extra = f"; loopback exceptions {', '.join(loopback)}" if loopback else ""
        lines.append(f"Segment {number} ({segment.kind.value}): egress allowed {allowed}{extra}")
    return lines


def _recorded(workflow: WorkflowVersion | None, index: int) -> str | None:
    if workflow is None or index >= len(workflow.steps):
        return None
    fingerprint = step_target(workflow.steps[index])
    if fingerprint is None:
        return None
    role = fingerprint.role.value if fingerprint.role is not None else fingerprint.tag
    return f'{role} "{fingerprint.accessible_name or ""}"'


def _describe(identity: IdentityReport) -> str:
    return f'{identity.role or identity.tag} "{identity.name}"'


def _numbers(proposal: HealProposal) -> str:
    parts = [f"score {proposal.candidate.score:.2f}"]
    if proposal.margin is not None:
        parts.append(f"margin {proposal.margin:.2f}")
    if proposal.threshold is not None:
        parts.append(f"threshold {proposal.threshold:.2f}")
    if proposal.required_margin is not None:
        parts.append(f"required margin {proposal.required_margin:.2f}")
    return " · ".join(parts)


def _paused_screenshot(run: Run, proposal: HealProposal) -> ArtifactName | None:
    paused = [step for step in run.paused_steps if step.index == proposal.step_index]
    if paused:
        return paused[-1].artifacts.screenshot
    step = run.steps[proposal.step_index] if proposal.step_index < len(run.steps) else None
    if step is not None and run.status is RunStatus.AWAITING_APPROVAL:
        return step.artifacts.screenshot
    return None
