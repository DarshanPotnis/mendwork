"""A workflow store on disk for the CLI tests of history, diff, rollback, and import (ADR 0013).

The ledger's v1, and v2 in which a heal renamed its export button to "Share ledger"; optionally a
pending heal of v2's export step to a button named "Export", verified by one run.
"""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Final

from mendwork.adapters.storage_fs.pending_patches import FilePendingPatches
from mendwork.adapters.storage_fs.workflow_store import FileWorkflowStore
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.lineage import heal_version
from mendwork.engine.domain.run_identifiers import parse_run_id
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import VersionConflict
from mendwork.engine.patching.promotion import started
from mendwork.engine.patching.sources import stored_history
from mendwork.settings import Settings
from tests.fakes.clock import FakeClock
from tests.heal_changes import heal_change, renamed_target, target_of
from tests.unit.patching.builders import RENAMED, WORKFLOW_ID, ledger_version
from tests.workflows import CREATED_AT_DATETIME

CODEC: Final = WorkflowYamlCodec(max_bytes=1 << 20)
CLOCK: Final = FakeClock(CREATED_AT_DATETIME + timedelta(days=1))
PENDING_RUN: Final = parse_run_id("20260915T100000Z-00000001")


def healed(root: Path) -> tuple[WorkflowVersion, WorkflowVersion]:
    """Store the ledger's v1, and v2 in which a heal renamed its export button."""
    first = ledger_version()
    new_target = renamed_target(target_of(first, "export"), RENAMED)
    second = heal_version(first, heal_change(first, "export", new_target=new_target), clock=CLOCK)
    publish(root, first, second)
    return first, second


def publish(root: Path, *versions: WorkflowVersion) -> None:
    """Store versions under ``root``, in order."""

    async def store() -> None:
        for version in versions:
            await FileWorkflowStore(root, CODEC).publish(version)

    asyncio.run(store())


def pend(root: Path, workflow: WorkflowVersion) -> None:
    """A pending heal of the export step to a button named "Export", verified by one run."""
    new_target = renamed_target(target_of(workflow, "export"), "Export")
    change = heal_change(workflow, "export", new_target=new_target)
    patch = started(workflow.workflow_id, workflow.steps[1], change, PENDING_RUN, CLOCK.now())

    async def store() -> None:
        async with FilePendingPatches(root).hold(workflow.workflow_id) as hold:
            await hold.replace([patch])

    asyncio.run(store())


def history_of(root: Path) -> tuple[WorkflowVersion, ...]:
    """Every stored version of the ledger, oldest first."""
    return asyncio.run(stored_history(FileWorkflowStore(root, CODEC), WORKFLOW_ID))


class PublishedFirstByAnother(FileWorkflowStore):
    """A store to which another process publishes between a command's read and its publish."""

    async def publish(self, version: WorkflowVersion) -> None:
        raise VersionConflict("the version number is taken", reason="exists")


def raced_store(settings: Settings, root: Path) -> FileWorkflowStore:
    """Stands in for ``patch_wiring.workflow_store`` when another process always publishes first."""
    return PublishedFirstByAnother(root, CODEC)
