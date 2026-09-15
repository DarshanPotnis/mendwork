"""Recording a person's decision on a proposal, then resuming or ending the run (ADR 0011).

Every decision is made while the run is claimed, so no other process can decide on it or resume it
at the same time, and in this order:

1. the record is read back and completed from its journal and the audit log, because a process can
   stop between any two writes;
2. the proposal must still be pending. An approval also needs a resumable run, its saved workflow
   unchanged, its secrets available, and every URL known up front allowed by today's egress policy.
   If anything fails here, nothing is recorded;
3. the decision is appended to the audit log first, then written to the run's record. An interrupt
   waits until both writes are done, so a record that says approved always has the entry that
   proves it, and a process that stops between the two leaves the entry for step 1 to finish;
4. an approval resumes the run at once (``resume``); a rejection ends it failed.
"""

import asyncio
import hashlib

import structlog
from pydantic import ValidationError

from mendwork.engine.domain.approvals import REASON_MAX_LENGTH, DecisionKind, ProposalDecision
from mendwork.engine.domain.audit import AuditDraft, AuditKind
from mendwork.engine.domain.heals import HealProposal
from mendwork.engine.domain.runs import Run, RunId
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import ProposalNotPending, RunNotResumable, SecretUnavailable
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.ports.audit import AuditLog
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.run_records import RunRecords
from mendwork.engine.ports.secrets import SecretResolver
from mendwork.engine.replay.approval_records import as_left, decided, not_resumed, rejected
from mendwork.engine.replay.artifact_names import RUN_RECORD
from mendwork.engine.replay.journal import encode_run
from mendwork.engine.replay.resume import PreparedResume, Resumer
from mendwork.engine.safety.approvals import (
    NotResumable,
    Problem,
    decision_problem,
    find_proposal,
    record_problem,
    snapshot_problem,
)
from mendwork.engine.safety.secret_scrub import SecretScrubber


class ApprovalDesk:
    """Where people approve or reject proposals, and read a run as it stands."""

    def __init__(
        self,
        *,
        records: RunRecords,
        audit: AuditLog,
        artifacts: ArtifactStore,
        secrets: SecretResolver,
        clock: Clock,
        scrubber: SecretScrubber,
    ) -> None:
        self._records = records
        self._audit = audit
        self._artifacts = artifacts
        self._secrets = secrets
        self._clock = clock
        self._scrubber = scrubber
        self._log = structlog.stdlib.get_logger("mendwork.approvals")

    async def current(self, run_id: RunId) -> Run:
        """The run as it stands, without writing anything.

        A record no process holds is completed from its journal and the audit log, so a run whose
        process stopped reads as it was left rather than as still running.

        Raises UnknownRun when the run has no record, and AuditLogCorrupt when the log is broken.
        """
        stored = await self._records.load_run(run_id)
        if await self._records.is_claimed(run_id):
            return stored
        return as_left(stored, await self._audit.entries_for_run(run_id), at=self._clock.now())

    async def approve(self, run_id: RunId, proposal_id: str, resumer: Resumer) -> Run:
        """Approve a pending proposal, resume the run with ``resumer``, and return its record.

        Raises RunBusy, UnknownRun, ProposalNotPending, RunNotResumable, RunInputError,
        SecretUnavailable, EgressBlocked, or AuditLogCorrupt before anything is recorded.
        """
        async with self._records.claim(run_id):
            run = await self._completed(run_id)
            proposal = _pending(run, proposal_id)
            prepared = await self._prepare(run, proposal, resumer)
            approved = await self._whole(run, proposal, DecisionKind.APPROVED, None)
            return await resumer.resume(prepared, approved)

    async def reject(self, run_id: RunId, proposal_id: str, reason: str | None) -> Run:
        """Reject a pending proposal, which ends its run failed, and return the record.

        The reason is recorded in the audit log and the run's record, scrubbed of the run's secrets.
        Raises RunBusy, UnknownRun, ProposalNotPending, or AuditLogCorrupt before anything is
        recorded.
        """
        async with self._records.claim(run_id):
            run = await self._completed(run_id)
            proposal = _pending(run, proposal_id)
            scrubbed = await self._scrubbed(run, reason)
            return await self._whole(run, proposal, DecisionKind.REJECTED, scrubbed)

    async def _completed(self, run_id: RunId) -> Run:
        stored = await self._records.load_run(run_id)
        completed = as_left(stored, await self._audit.entries_for_run(run_id), at=self._clock.now())
        if completed != stored:
            await self._write(completed)
        return completed

    async def _prepare(self, run: Run, proposal: HealProposal, resumer: Resumer) -> PreparedResume:
        _refuse_resume(record_problem(run, proposal), run, proposal)
        data = await self._records.load_workflow(run.run_id)
        try:
            workflow = WorkflowVersion.model_validate_json(data)
        except ValidationError as error:
            raise RunNotResumable(
                f"the workflow saved with run {run.run_id} cannot be read",
                reason=NotResumable.WORKFLOW_UNREADABLE.value,
                run_id=run.run_id,
                proposal_id=proposal.id,
            ) from error
        digest = hashlib.sha256(data).hexdigest()
        _refuse_resume(snapshot_problem(run, workflow, digest, proposal), run, proposal)
        return await resumer.prepare(run, workflow, proposal)

    async def _whole(
        self, run: Run, proposal: HealProposal, kind: DecisionKind, reason: str | None
    ) -> Run:
        """Record the decision with both of its writes, even if an interrupt arrives meanwhile.

        An approval interrupted this way is recorded, and its run cancelled before it resumed.
        """
        task = asyncio.ensure_future(self._decide(run, proposal, kind, reason))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            recorded = await task
            if kind is DecisionKind.APPROVED:
                await self._write(not_resumed(recorded, proposal.id, at=self._clock.now()))
            raise

    async def _decide(
        self, run: Run, proposal: HealProposal, kind: DecisionKind, reason: str | None
    ) -> Run:
        entry = await self._audit.append(
            AuditDraft(
                kind=(
                    AuditKind.PROPOSAL_APPROVED
                    if kind is DecisionKind.APPROVED
                    else AuditKind.PROPOSAL_REJECTED
                ),
                at=self._clock.now(),
                run_id=run.run_id,
                workflow_id=run.workflow_id,
                workflow_version=run.workflow_version,
                step_id=proposal.step_id,
                step_index=proposal.step_index,
                proposal_id=proposal.id,
                reason=reason,
            )
        )
        decision = ProposalDecision(
            kind=kind, at=entry.at, audit_sequence=entry.sequence, reason=entry.reason
        )
        if kind is DecisionKind.APPROVED:
            updated = decided(run, proposal.id, decision)
        else:
            updated = rejected(run, proposal.id, decision)
        await self._write(updated)
        self._log.info(
            "proposal_decided",
            run_id=run.run_id,
            step_id=proposal.step_id,
            proposal_id=proposal.id,
            decision=kind.value,
            audit_sequence=entry.sequence,
        )
        return updated

    async def _scrubbed(self, run: Run, reason: str | None) -> str | None:
        if not reason:
            return None
        for name in run.secrets:
            try:
                self._scrubber.register(await self._secrets.resolve(name))
            except SecretUnavailable:
                self._log.warning(
                    "rejection_reason_not_scrubbed_of_secret", run_id=run.run_id, secret=name
                )
        return self._scrubber.scrub_text(reason)[:REASON_MAX_LENGTH]

    async def _write(self, run: Run) -> None:
        await self._artifacts.write(run.run_id, RUN_RECORD, encode_run(run))


def _pending(run: Run, proposal_id: str) -> HealProposal:
    problem = decision_problem(run, proposal_id)
    found = find_proposal(run, proposal_id)
    if problem is not None or found is None:
        reason = problem.reason if problem is not None else "unknown_proposal"
        message = problem.message if problem is not None else f"no proposal {proposal_id}"
        raise ProposalNotPending(message, reason=reason, run_id=run.run_id, proposal_id=proposal_id)
    return found.proposal


def _refuse_resume(problem: Problem | None, run: Run, proposal: HealProposal) -> None:
    if problem is not None:
        raise RunNotResumable(
            problem.message, reason=problem.reason, run_id=run.run_id, proposal_id=proposal.id
        )
