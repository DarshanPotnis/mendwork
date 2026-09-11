"""``mendwork schema``: write the workflow JSON Schema that editors use for autocomplete."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from mendwork.engine.domain.json_schema import workflow_json_schema_text


def schema(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", dir_okay=False, help="Where to write the schema."),
    ],
) -> None:
    """Write the JSON Schema for workflow files, generated from the domain models."""
    text = workflow_json_schema_text()
    asyncio.run(asyncio.to_thread(output.write_text, text, encoding="utf-8"))
    typer.echo(f"wrote {output}")
