"""``mendwork validate``: check a workflow file and explain every problem with its line."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import ValidationIssue, WorkflowValidationError
from mendwork.settings import Settings


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def summarize(source: str, version: WorkflowVersion) -> str:
    """The one-line report for a valid workflow."""
    checkpoints = sum(len(step.checkpoints) for step in version.steps)
    counts = ", ".join(
        (
            _plural(len(version.steps), "step"),
            _plural(len(version.inputs), "input"),
            _plural(len(version.secrets), "secret"),
            _plural(checkpoints, "checkpoint"),
        )
    )
    return f"OK {source}: {version.workflow_id} v{version.version} — {counts}"


def format_issue(source: str, issue: ValidationIssue) -> str:
    """One problem as ``file:line:column: path (step id): message``, clickable in editors."""
    where = source
    if issue.line is not None:
        where += f":{issue.line}"
        if issue.column is not None:
            where += f":{issue.column}"
    subject = issue.path
    if issue.step_id is not None:
        subject += f" (step {issue.step_id})"
    return f"{where}: {subject}: {issue.message}" if subject else f"{where}: {issue.message}"


def format_problems(source: str, error: WorkflowValidationError) -> str:
    """Every problem in a rejected workflow, one per line, under a count."""
    issues = error.issues or (ValidationIssue((), error.message),)
    lines = [f"{source}: {_plural(len(issues), 'problem')}"]
    lines.extend(format_issue(source, issue) for issue in issues)
    return "\n".join(lines)


async def read_limited(path: Path, limit: int) -> bytes:
    def read() -> bytes:
        with path.open("rb") as file:
            # One byte past the limit is enough for the codec to report the size.
            return file.read(limit + 1)

    return await asyncio.to_thread(read)


def validate(
    workflow: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="Workflow YAML file."),
    ],
) -> None:
    """Validate a workflow file; print a summary, or every problem with its line and exit 1."""
    codec = WorkflowYamlCodec(max_bytes=Settings().workflow_max_bytes)
    source = str(workflow)
    content = asyncio.run(read_limited(workflow, codec.max_bytes))
    try:
        version = codec.decode(content, source=source)
    except WorkflowValidationError as error:
        typer.echo(format_problems(source, error))
        raise typer.Exit(code=1) from None
    typer.echo(summarize(source, version))
