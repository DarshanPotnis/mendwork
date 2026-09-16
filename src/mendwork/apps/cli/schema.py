"""``mendwork schema``: write the JSON Schemas generated from the domain models."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from mendwork.engine.benchmark.results import results_json_schema_text
from mendwork.engine.domain.json_schema import workflow_json_schema_text


def schema(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", dir_okay=False, help="Where to write the workflow schema."),
    ],
    results_output: Annotated[
        Path | None,
        typer.Option(
            "--results-output",
            dir_okay=False,
            help="Where to write the benchmark results schema (ADR 0014).",
        ),
    ] = None,
) -> None:
    """Write the JSON Schema for workflow files, and optionally for benchmark results."""
    asyncio.run(asyncio.to_thread(output.write_text, workflow_json_schema_text(), encoding="utf-8"))
    typer.echo(f"wrote {output}")
    if results_output is not None:
        text = results_json_schema_text()
        asyncio.run(asyncio.to_thread(results_output.write_text, text, encoding="utf-8"))
        typer.echo(f"wrote {results_output}")
