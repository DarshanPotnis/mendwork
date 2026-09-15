"""``mendwork history``: a workflow's versions, newest first, with why each exists (ADR 0013).

It also shows how strongly each step of the latest version is checked, and the heals waiting to
become versions. It reads the workflow store and changes nothing. Exit codes are in
``store_commands``.
"""

import asyncio
import sys
from pathlib import Path
from typing import TextIO

from mendwork.apps.cli.commands import OutputMode, settings_or_exit
from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.apps.cli.patch_wiring import pending_patches, store_directory, workflow_store
from mendwork.apps.cli.store_commands import (
    StoreOption,
    WorkflowIdArgument,
    fail,
    store_failure,
    workflow_id_or_exit,
)
from mendwork.apps.cli.version_output import history_text
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.patching.sources import STORE_ERRORS, stored_history
from mendwork.settings import Settings


def history(workflow_id: WorkflowIdArgument, store_dir: StoreOption = None) -> None:
    """List a workflow's versions, newest first, with why each exists."""
    stdout, stderr = sys.stdout, sys.stderr
    settings = settings_or_exit(OutputMode.HUMAN, stdout, stderr)
    identifier = workflow_id_or_exit(workflow_id, stderr)
    root = store_directory(settings, store_dir)
    stdout.write(asyncio.run(_history(identifier, settings, root, stderr)) + "\n")


async def _history(workflow_id: WorkflowId, settings: Settings, root: Path, stderr: TextIO) -> str:
    try:
        versions = await stored_history(workflow_store(settings, root), workflow_id)
        pending = await pending_patches(root).read(workflow_id)
    except STORE_ERRORS as error:
        store_failure(stderr, error)
    if not versions:
        fail(stderr, ExitCode.INVALID, f"{root} has no versions of {workflow_id}.")
    return history_text(
        versions,
        pending,
        promotion=settings.patch_promotion,
        required=settings.patch_promotion_successes,
        store=root,
    )
