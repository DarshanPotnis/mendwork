"""``mendwork import``: make a workflow file its workflow's latest stored version (ADR 0013).

A file edited by hand matches no stored version, so it runs as written and saves no heals.
Importing it makes it the latest version, and its runs save heals again. It is the one command that
can cut a file loose from the versions heals made, so it first says what it will do: the version it
creates, each step that differs from the latest version, and the pending patches on those steps that
will stop matching. Then it asks; ``--yes`` answers for scripts, and anything but yes, including the
end of input, imports nothing. Exit codes are in ``store_commands``; 1 means the answer was no, or
another process published a version first.
"""

import asyncio
import sys
from pathlib import Path
from typing import Annotated, TextIO

import typer

from mendwork.adapters.storage_fs.workflow_store import FileWorkflowStore
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.commands import OutputMode, settings_or_exit
from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.apps.cli.patch_wiring import pending_patches, store_directory, workflow_store
from mendwork.apps.cli.store_commands import (
    NOT_CHANGED,
    StoreOption,
    fail,
    store_failure,
    text_or_exit,
)
from mendwork.apps.cli.validate import format_problems, read_limited
from mendwork.apps.cli.version_output import import_text
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import VersionConflict, WorkflowValidationError
from mendwork.engine.patching.manual_versions import AlreadyLatest, ImportPlan, plan_import
from mendwork.engine.patching.sources import STORE_ERRORS, stored_history
from mendwork.settings import Settings

_YES = frozenset({"y", "yes"})


def import_workflow(
    workflow: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="Workflow YAML file."),
    ],
    yes: Annotated[bool, typer.Option("--yes", help="Import without asking, for scripts.")] = False,
    summary: Annotated[
        str | None,
        typer.Option(
            "--summary",
            metavar="TEXT",
            help="What changed and why, in one line, for the history; the file's name by default.",
        ),
    ] = None,
    store_dir: StoreOption = None,
) -> None:
    """Make a workflow file its workflow's latest stored version, after showing what changes."""
    stdout, stderr = sys.stdout, sys.stderr
    settings = settings_or_exit(OutputMode.HUMAN, stdout, stderr)
    path = str(workflow)
    note = text_or_exit(
        "--summary", summary if summary is not None else f"Imported from {workflow.name}", stderr
    )
    root = store_directory(settings, store_dir)
    file = _load(workflow, settings, stderr)
    store = workflow_store(settings, root)
    plan = asyncio.run(_plan(store, file, path, root, note, stderr))
    if isinstance(plan, AlreadyLatest):
        stdout.write(
            f"{path} is already {file.workflow_id} v{plan.version}, the latest stored version; "
            "nothing was imported.\n"
        )
        return
    stdout.write(import_text(plan, path, root) + "\n")
    if not yes and not _confirmed(stdout):
        stdout.write("Nothing was imported.\n")
        raise typer.Exit(code=NOT_CHANGED)
    asyncio.run(_publish(store, plan, stderr))
    stdout.write(f"Imported {path} as {file.workflow_id} v{plan.version.version}.\n")


def _load(workflow: Path, settings: Settings, stderr: TextIO) -> WorkflowVersion:
    codec = WorkflowYamlCodec(max_bytes=settings.workflow_max_bytes)
    content = asyncio.run(read_limited(workflow, codec.max_bytes))
    try:
        return codec.decode(content, source=str(workflow))
    except WorkflowValidationError as error:
        fail(stderr, ExitCode.INVALID, format_problems(str(workflow), error))


async def _plan(
    store: FileWorkflowStore,
    file: WorkflowVersion,
    path: str,
    root: Path,
    summary: str,
    stderr: TextIO,
) -> ImportPlan | AlreadyLatest:
    try:
        history = await stored_history(store, file.workflow_id)
        pending = await pending_patches(root).read(file.workflow_id)
    except STORE_ERRORS as error:
        store_failure(stderr, error)
    try:
        return plan_import(file, history, pending, summary=summary, clock=SystemClock())
    except WorkflowValidationError as error:
        fail(
            stderr,
            ExitCode.INVALID,
            f"{format_problems(path, error)}\nNothing was imported: {file.workflow_id} "
            f"v{history[-1].version} is the latest stored version, and every version keeps its "
            "step ids.",
        )


async def _publish(store: FileWorkflowStore, plan: ImportPlan, stderr: TextIO) -> None:
    version = plan.version
    try:
        await store.publish(version)
    except VersionConflict:
        fail(
            stderr,
            NOT_CHANGED,
            f"Nothing was imported: another process published {version.workflow_id} "
            f"v{version.version} first. Run mendwork import again to see what it would change now.",
        )
    except STORE_ERRORS as error:
        store_failure(stderr, error)


def _confirmed(stdout: TextIO) -> bool:
    stdout.write("Import? [y/N] ")
    stdout.flush()
    answer = sys.stdin.readline()
    if not answer.endswith("\n"):
        stdout.write("\n")
    return answer.strip().lower() in _YES
