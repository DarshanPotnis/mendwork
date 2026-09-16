"""Benchmark results on disk: atomic replacement, and a read that goes back through the schema."""

import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from mendwork.adapters.benchmark_fs.files import (
    encode_results,
    read_results,
    replace_file,
    write_results,
)
from mendwork.engine.errors import ArtifactStoreUnavailable
from tests.unit.reporting.test_scorecard_view import grid_document


def test_results_written_are_read_back_identically(tmp_path: Path) -> None:
    path = tmp_path / "results" / "chaos-results.json"

    asyncio.run(write_results(path, grid_document()))

    assert asyncio.run(read_results(path)) == grid_document()
    assert path.read_bytes() == encode_results(grid_document())
    assert path.read_bytes().endswith(b"}\n")


def test_a_file_is_replaced_whole_and_no_temporary_file_is_left(tmp_path: Path) -> None:
    path = tmp_path / "scorecard.html"
    path.write_text("old", encoding="utf-8")

    replace_file(path, b"new")

    assert path.read_bytes() == b"new"
    assert [item.name for item in tmp_path.iterdir()] == ["scorecard.html"]


def test_a_write_that_fails_part_way_leaves_the_old_file_and_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "chaos-results.json"
    path.write_text("old", encoding="utf-8")

    def refuse(descriptor: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", refuse)

    with pytest.raises(ArtifactStoreUnavailable, match="could not write the benchmark results"):
        asyncio.run(write_results(path, grid_document()))

    assert path.read_text(encoding="utf-8") == "old"
    assert [item.name for item in tmp_path.iterdir()] == ["chaos-results.json"]


def test_a_results_file_that_cannot_be_read_is_reported_as_unavailable(tmp_path: Path) -> None:
    with pytest.raises(ArtifactStoreUnavailable, match="could not read"):
        asyncio.run(read_results(tmp_path / "missing.json"))


def test_a_results_file_that_is_not_a_results_document_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "chaos-results.json"
    path.write_text('{"schema_version": 2}', encoding="utf-8")

    with pytest.raises(ValidationError):
        asyncio.run(read_results(path))
