"""Benchmark results and scorecards on disk (ADR 0014).

A results file is replaced atomically: written to a temporary file beside it, synced, and renamed
over the old one, so a benchmark stopped half way never leaves a truncated document that a README
links to. Reading goes back through the results schema, which also checks every section's digest.
"""

import asyncio
import os
import tempfile
from pathlib import Path

from mendwork.engine.benchmark.results import BenchmarkResults
from mendwork.engine.errors import ArtifactStoreUnavailable


def replace_file(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, replacing any file there. Blocking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    written = False
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        Path(temporary).replace(path)
        written = True
    finally:
        if not written:
            Path(temporary).unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def encode_results(results: BenchmarkResults) -> bytes:
    """The document as committed: indented JSON and a final newline."""
    return (results.model_dump_json(indent=2) + "\n").encode("utf-8")


async def write_results(path: Path, results: BenchmarkResults) -> None:
    """Replace the results file at ``path``."""
    await write_bytes(path, encode_results(results), what="benchmark results")


async def write_bytes(path: Path, data: bytes, *, what: str) -> None:
    """Replace a file, reporting a failure as the artifacts being unavailable."""
    try:
        await asyncio.to_thread(replace_file, path, data)
    except OSError as error:
        raise ArtifactStoreUnavailable(
            f"could not write the {what}", path=str(path), detail=str(error)
        ) from error


async def read_results(path: Path) -> BenchmarkResults:
    """A results document, validated against the schema and its digests."""
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as error:
        raise ArtifactStoreUnavailable(
            "could not read the benchmark results", path=str(path), detail=str(error)
        ) from error
    return BenchmarkResults.model_validate_json(data)
