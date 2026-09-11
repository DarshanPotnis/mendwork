"""Change records: why a workflow version other than the first exists.

A discriminated union, so each kind of change carries exactly its own evidence. Phase 8
adds a heal variant; every ``match`` over ``ChangeRecord`` then fails type checking until
it handles the new kind, which is how the extension stays safe without a placeholder.
"""

from typing import Annotated, Literal

from pydantic import Field

from mendwork.engine.domain.base import DomainModel, Text
from mendwork.engine.domain.enums import ChangeKind
from mendwork.engine.domain.identifiers import VersionNumber


class ManualEdit(DomainModel):
    """A person edited the workflow's content by hand."""

    kind: Literal[ChangeKind.MANUAL_EDIT]
    summary: Text
    """What changed and why, for the version history."""


class Rollback(DomainModel):
    """The content of an earlier version was restored as a new version."""

    kind: Literal[ChangeKind.ROLLBACK]
    restored_version: VersionNumber
    """The version whose content this version carries."""
    reason: Text


ChangeRecord = Annotated[ManualEdit | Rollback, Field(discriminator="kind")]


def describe_change(change: ChangeRecord) -> str:
    """A one-line account of a change, for version history listings."""
    match change:
        case ManualEdit():
            return f"manual edit: {change.summary}"
        case Rollback():
            return f"rollback to v{change.restored_version}: {change.reason}"
