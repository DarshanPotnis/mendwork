"""The ``mendwork`` command-line entry point.

A composition root: it reads configuration, installs logging, and will wire adapters
into the engine as commands arrive in later phases. It holds no business logic.
"""

from importlib.metadata import version as package_version
from typing import Annotated

import typer

from mendwork.observability import configure_logging
from mendwork.settings import Settings

app = typer.Typer(
    name="mendwork",
    help="Self-healing browser automation: record once, replay free, repair cheaply.",
    no_args_is_help=True,
)


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
    configure_logging(Settings())
