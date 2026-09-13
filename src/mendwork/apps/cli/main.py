"""The ``mendwork`` command-line entry point.

A composition root: it reads configuration, installs logging, and wires adapters into the
engine through its commands. It holds no business logic.
"""

from importlib.metadata import version as package_version
from typing import Annotated

import typer
from pydantic import ValidationError

from mendwork.apps.cli.exit_codes import ExitCode
from mendwork.apps.cli.record import build_record_command
from mendwork.apps.cli.record_runtime import production_dependencies
from mendwork.apps.cli.run import run
from mendwork.apps.cli.schema import schema
from mendwork.apps.cli.validate import validate
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
app.command(name="record")(build_record_command(production_dependencies()))


def _print_version(requested: bool) -> None:
    if requested:
        typer.echo(f"mendwork {package_version('mendwork')}")
        raise typer.Exit


@app.callback()
def main(
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
    configure_logging(settings)
