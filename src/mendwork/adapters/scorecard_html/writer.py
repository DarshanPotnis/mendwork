"""Writing the benchmark scorecard (ADR 0014).

The scorecard is rebuilt from results documents by a pure view model and written atomically beside
them. Its stylesheet ships as package data beside this module.
"""

import asyncio
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Final

from mendwork.adapters.benchmark_fs.files import write_bytes
from mendwork.adapters.scorecard_html.render import render_scorecard
from mendwork.engine.benchmark.results import BenchmarkResults
from mendwork.engine.reporting.scorecard_view import scorecard_view

SCORECARD_PACKAGE: Final = "mendwork.adapters.scorecard_html"


def load_css() -> str:
    """The scorecard's stylesheet, from the installed package. Blocking."""
    return (files(SCORECARD_PACKAGE) / "scorecard.css").read_text(encoding="utf-8")


def scorecard_document(documents: Sequence[BenchmarkResults]) -> str:
    """The scorecard for the documents, in the order given. Blocking: it reads the stylesheet."""
    return render_scorecard(scorecard_view(documents), css=load_css())


async def write_scorecard(path: Path, documents: Sequence[BenchmarkResults]) -> None:
    """Render the scorecard and replace the file at ``path`` with it."""
    document = await asyncio.to_thread(scorecard_document, documents)
    await write_bytes(path, document.encode("utf-8"), what="scorecard")
