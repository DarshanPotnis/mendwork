"""``mendwork diff``: what changed between two versions of a workflow, in plain words (ADR 0013).

Steps are compared by id. For a step whose element changed it shows what the element is and how it
is found on each side, and for a heal, why it was made. It reads the workflow store and changes
nothing. Exit codes are in ``store_commands``.
"""

import asyncio
import sys
from pathlib import Path
from typing import Annotated, TextIO

import typer

from mendwork.apps.cli.commands import OutputMode, settings_or_exit
from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.apps.cli.patch_wiring import store_directory, workflow_store
from mendwork.apps.cli.store_commands import (
    StoreOption,
    WorkflowIdArgument,
    fail,
    store_failure,
    version_number_or_exit,
    workflow_id_or_exit,
)
from mendwork.apps.cli.version_output import diff_text
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.errors import UnknownWorkflowVersion
from mendwork.engine.patching.diff import diff_versions
from mendwork.engine.patching.manual_versions import stored_version
from mendwork.engine.patching.sources import STORE_ERRORS, stored_history
from mendwork.settings import Settings


def diff(
    workflow_id: WorkflowIdArgument,
    before: Annotated[
        str, typer.Argument(metavar="FROM", help="The earlier version, such as 1 or v1.")
    ],
    after: Annotated[
        str | None,
        typer.Argument(metavar="TO", help="The later version; the latest when left out."),
    ] = None,
    store_dir: StoreOption = None,
) -> None:
    """Show what changed between two versions of a workflow."""
    stdout, stderr = sys.stdout, sys.stderr
    settings = settings_or_exit(OutputMode.HUMAN, stdout, stderr)
    identifier = workflow_id_or_exit(workflow_id, stderr)
    first = version_number_or_exit(before, stderr)
    second = None if after is None else version_number_or_exit(after, stderr)
    root = store_directory(settings, store_dir)
    stdout.write(asyncio.run(_diff(identifier, first, second, settings, root, stderr)) + "\n")


async def _diff(
    workflow_id: WorkflowId,
    first: int,
    second: int | None,
    settings: Settings,
    root: Path,
    stderr: TextIO,
) -> str:
    try:
        history = await stored_history(workflow_store(settings, root), workflow_id)
    except STORE_ERRORS as error:
        store_failure(stderr, error)
    try:
        earlier = stored_version(workflow_id, history, first)
        later = history[-1] if second is None else stored_version(workflow_id, history, second)
    except UnknownWorkflowVersion as error:
        fail(stderr, ExitCode.INVALID, f"{error.message}.")
    return diff_text(diff_versions(earlier, later, history))
