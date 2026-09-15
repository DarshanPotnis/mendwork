"""What ``mendwork run`` says about the workflow store before a run starts (ADR 0013)."""

from pathlib import Path

from mendwork.engine.domain.patches import NotSavedReason
from mendwork.engine.patching.sources import SourceDecision


def source_notice(decision: SourceDecision, store: Path) -> str | None:
    """Why the run executes the version it does; None when it simply runs the file as given."""
    source, version = decision.source, decision.version
    workflow_id = version.workflow_id
    path = source.path or "the file"
    if decision.publish_first:
        return f"Stored {path} as {workflow_id} v1 in {store}; heals from its runs are saved there."
    if source.saves_heals:
        if version.version == source.stored_version:
            return None
        return (
            f"Running {workflow_id} v{version.version} from {store}: {path} is "
            f"v{source.stored_version}, and every later version came from a heal or a rollback."
        )
    match source.not_saved:
        case NotSavedReason.FILE_DIFFERS:
            return (
                f"{path} differs from every stored version of {workflow_id}, so it runs as written "
                f"and its heals are not saved; mendwork import {path} makes it the latest version."
            )
        case NotSavedReason.NEWER_IMPORT:
            return (
                f"{path} is {workflow_id} v{source.stored_version}, but a later version was "
                "imported, so it runs as written and its heals are not saved."
            )
        case NotSavedReason.NO_LINEAGE:
            return (
                f"{store} has no versions of {workflow_id} and {path} is not a first version, so "
                f"it runs as written and its heals are not saved; mendwork import {path} stores it."
            )
        case NotSavedReason.STORE_UNAVAILABLE:
            return (
                f"The workflow store {store} could not be used, so {path} runs as written and its "
                "heals are not saved."
            )
        case NotSavedReason.EXACT | None:
            return None
