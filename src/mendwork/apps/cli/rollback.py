"""``mendwork rollback``: restore an earlier version's content as a new version (ADR 0013).

The new version follows the latest version, whatever was published since a run was examined, and
the command first lists every version it undoes. Nothing is deleted, so a rollback is undone by
rolling back again. A heal a rollback undoes is never saved again automatically. Exit codes are in
``store_commands``; 1 means another process published a version while this one was being stored.
"""

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Final, TextIO

import typer

from mendwork.adapters.system.clock import SystemClock
from mendwork.apps.cli.commands import OutputMode, settings_or_exit
from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.apps.cli.patch_wiring import pending_patches, store_directory, workflow_store
from mendwork.apps.cli.store_commands import (
    NOT_CHANGED,
    StoreOption,
    WorkflowIdArgument,
    fail,
    store_failure,
    text_or_exit,
    version_number_or_exit,
    workflow_id_or_exit,
)
from mendwork.apps.cli.version_output import rollback_text
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.errors import UnknownWorkflowVersion, VersionConflict, WorkflowValidationError
from mendwork.engine.patching.manual_versions import plan_rollback
from mendwork.engine.patching.sources import STORE_ERRORS, stored_history
from mendwork.settings import Settings

DEFAULT_REASON: Final = "no reason given"


def rollback(
    workflow_id: WorkflowIdArgument,
    to: Annotated[
        str,
        typer.Option(
            "--to", metavar="VERSION", help="The version whose content to restore, such as 2."
        ),
    ],
    reason: Annotated[
        str,
        typer.Option("--reason", metavar="TEXT", help="Why, in one line, kept in the history."),
    ] = DEFAULT_REASON,
    store_dir: StoreOption = None,
) -> None:
    """Restore an earlier version's content as a new version; nothing is deleted."""
    stdout, stderr = sys.stdout, sys.stderr
    settings = settings_or_exit(OutputMode.HUMAN, stdout, stderr)
    identifier = workflow_id_or_exit(workflow_id, stderr)
    number = version_number_or_exit(to, stderr)
    text = text_or_exit("--reason", reason, stderr)
    root = store_directory(settings, store_dir)
    asyncio.run(_rollback(identifier, number, text, settings, root, stdout, stderr))


async def _rollback(
    workflow_id: WorkflowId,
    number: int,
    reason: str,
    settings: Settings,
    root: Path,
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    store = workflow_store(settings, root)
    try:
        history = await stored_history(store, workflow_id)
        pending = await pending_patches(root).read(workflow_id)
    except STORE_ERRORS as error:
        store_failure(stderr, error)
    try:
        plan = plan_rollback(
            workflow_id, history, number, pending, reason=reason, clock=SystemClock()
        )
    except (UnknownWorkflowVersion, WorkflowValidationError) as error:
        fail(stderr, ExitCode.INVALID, f"Nothing was rolled back: {error.message}.")
    stdout.write(rollback_text(plan) + "\n")
    created = plan.version.version
    try:
        await store.publish(plan.version)
    except VersionConflict:
        fail(
            stderr,
            NOT_CHANGED,
            f"Nothing was rolled back: another process published {workflow_id} v{created} first. "
            "Run the command again to see what it would undo now.",
        )
    except STORE_ERRORS as error:
        store_failure(stderr, error)
    latest = plan.latest.version
    stdout.write(
        f"Rolled back {workflow_id} to v{number} as v{created}. To restore v{latest}: "
        f"mendwork rollback {workflow_id} --to {latest}\n"
    )
