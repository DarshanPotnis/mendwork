"""Audit entries: every decision a person makes on a run, kept append-only (ARCHITECTURE §11).

Each entry records the decision, when it was made, the run, the workflow version, the step, the
proposal, and a rejection's reason; who decided arrives with workspaces and members (Phase 10).
Entries are numbered from 1 and chained: each carries the SHA-256 of the entry before it and of
itself, over canonical JSON, so an entry edited, removed, or reordered in place breaks the chain,
and a broken chain allows no further decision.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, JsonValue

from mendwork.engine.domain.approvals import REASON_MAX_LENGTH
from mendwork.engine.domain.base import DomainModel, check_single_line
from mendwork.engine.domain.heals import PROPOSAL_ID_PATTERN
from mendwork.engine.domain.identifiers import StepIdField, VersionNumber, WorkflowIdField
from mendwork.engine.domain.runs import RunIdField

AUDIT_VERSION: Final = 1
GENESIS_SHA256: Final = "0" * 64
"""What the first entry's previous digest is."""
_SHA256: Final = r"^[0-9a-f]{64}$"

Reason = Annotated[
    str, Field(min_length=1, max_length=REASON_MAX_LENGTH), AfterValidator(check_single_line)
]


class AuditKind(StrEnum):
    """What an entry records."""

    PROPOSAL_APPROVED = "proposal_approved"
    PROPOSAL_REJECTED = "proposal_rejected"


class AuditDraft(DomainModel):
    """A decision about to be recorded: everything but its place in the log."""

    kind: AuditKind
    at: datetime
    run_id: RunIdField
    workflow_id: WorkflowIdField
    workflow_version: VersionNumber
    step_id: StepIdField
    step_index: int = Field(ge=0)
    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    reason: Reason | None = None


class AuditEntry(AuditDraft):
    """A recorded decision and its place in the chain."""

    audit_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    previous_sha256: str = Field(pattern=_SHA256)
    sha256: str = Field(pattern=_SHA256)


def chained(draft: AuditDraft, *, sequence: int, previous_sha256: str) -> AuditEntry:
    """The draft as the entry after one whose digest is ``previous_sha256``."""
    unsigned: dict[str, JsonValue] = {
        **draft.model_dump(mode="json"),
        "audit_version": AUDIT_VERSION,
        "sequence": sequence,
        "previous_sha256": previous_sha256,
    }
    return AuditEntry.model_validate({**unsigned, "sha256": _digest(unsigned)})


def chain_problem(entries: Sequence[AuditEntry]) -> str | None:
    """Why the entries, in file order, are not an unbroken chain; None when they are."""
    previous = GENESIS_SHA256
    for position, entry in enumerate(entries, start=1):
        if entry.sequence != position:
            return f"entry {position} is numbered {entry.sequence}"
        if entry.previous_sha256 != previous:
            return f"entry {position} does not follow the entry before it"
        if _digest(entry.model_dump(mode="json", exclude={"sha256"})) != entry.sha256:
            return f"entry {position} was changed after it was recorded"
        previous = entry.sha256
    return None


def _digest(fields: Mapping[str, JsonValue]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()
