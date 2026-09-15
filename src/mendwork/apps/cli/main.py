"""The ``mendwork`` command-line entry point.

A composition root: it reads configuration, installs logging, and wires adapters into the
engine through its commands. It holds no business logic.
"""

from importlib.metadata import version as package_version
from typing import Annotated

import typer
from pydantic import ValidationError

from mendwork.apps.cli.approvals import approve, reject
from mendwork.apps.cli.diff import diff
from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.apps.cli.history import history
from mendwork.apps.cli.import_workflow import import_workflow
from mendwork.apps.cli.record import build_record_command
from mendwork.apps.cli.record_runtime import production_dependencies
from mendwork.apps.cli.rollback import rollback
from mendwork.apps.cli.run import run
from mendwork.apps.cli.schema import schema
from mendwork.apps.cli.show import show
from mendwork.apps.cli.validate import validate
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.observability import configure_logging
from mendwork.settings import Settings

app = typer.Typer(
    name="mendwork",
    help="Self-healing browser automation: record once, replay free, repair cheaply.",
    no_args_is_help=True,
)
app.command()(validate)
app.command()(schema)
app.command()(run)
app.command()(show)
app.command()(approve)
app.command()(reject)
app.command()(history)
app.command()(diff)
app.command()(rollback)
app.command(name="import")(import_workflow)
app.command(name="record")(build_record_command(production_dependencies()))


def _print_version(requested: bool) -> None:
    if requested:
        typer.echo(f"mendwork {package_version('mendwork')}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = False,
) -> None:
    """Run a Mendwork command."""
    try:
        settings = Settings()
    except ValidationError as error:
        typer.echo(f"invalid configuration: {error}", err=True)
        raise typer.Exit(code=ExitCode.INVALID) from None
    # One scrubber for the whole process: commands register every secret they resolve with it,
    # and the log pipeline removes those values from every line it writes.
    scrubber = SecretScrubber()
    configure_logging(settings, scrubber)
    ctx.obj = scrubber
