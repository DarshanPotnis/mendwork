"""What the commands that work with the workflow store share (ADR 0013).

``history``, ``diff``, ``rollback``, and ``import`` write their output to stdout and problems to
stderr. They exit 0 when done; 1 when a person declined, or another process published a version
first, so nothing was stored; 2 when the command line names something invalid or unknown; and 3
when the workflow store cannot be read or written.
"""

from pathlib import Path
from typing import Annotated, Final, NoReturn, TextIO

import typer

from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.engine.domain.base import check_single_line
from mendwork.engine.domain.identifiers import WorkflowId, parse_workflow_id
from mendwork.engine.domain.limits import TEXT_MAX_LENGTH
from mendwork.engine.errors import MendworkError, WorkflowValidationError

NOT_CHANGED: Final = 1
"""A person declined, or another process published a version first: nothing was stored."""

StoreOption = Annotated[
    Path | None,
    typer.Option(
        "--store-dir",
        file_okay=False,
        help="Where workflow versions are stored; MENDWORK_WORKFLOW_STORE_DIR by default.",
    ),
]
WorkflowIdArgument = Annotated[
    str,
    typer.Argument(metavar="WORKFLOW_ID", help="The workflow's id, as its file's workflow_id."),
]


def workflow_id_or_exit(value: str, stderr: TextIO) -> WorkflowId:
    """A workflow id from the command line, or exit 2."""
    try:
        return parse_workflow_id(value)
    except WorkflowValidationError as error:
        fail(stderr, ExitCode.INVALID, f"{value!r} is not a workflow id: {error.message}")


def version_number_or_exit(value: str, stderr: TextIO) -> int:
    """A version number written as 3 or v3, or exit 2."""
    digits = value.removeprefix("v")
    if not (digits.isascii() and digits.isdigit()) or int(digits) < 1:
        fail(stderr, ExitCode.INVALID, f"{value!r} is not a version number, such as 2 or v2")
    return int(digits)


def text_or_exit(option: str, value: str, stderr: TextIO) -> str:
    """One line of text a version records, such as a rollback's reason, or exit 2."""
    if not value.strip() or len(value) > TEXT_MAX_LENGTH:
        fail(stderr, ExitCode.INVALID, f"{option} must be 1 to {TEXT_MAX_LENGTH} characters")
    try:
        check_single_line(value)
    except ValueError as error:
        fail(stderr, ExitCode.INVALID, f"{option} {error}")
    return value


def store_failure(stderr: TextIO, error: MendworkError) -> NoReturn:
    """Exit 3: the workflow store could not be read or written, or holds a file it cannot read."""
    fail(stderr, ExitCode.INFRASTRUCTURE, f"{type(error).__name__}: {error.message}")


def fail(stderr: TextIO, code: int, message: str) -> NoReturn:
    """Exit with a message on stderr."""
    stderr.write(message + "\n")
    raise typer.Exit(code=code)
