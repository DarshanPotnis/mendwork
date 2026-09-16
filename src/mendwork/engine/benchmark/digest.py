"""The outcomes digest: one hash for everything a rerun must reproduce (ADR 0014).

It covers each cell's identity, run status, model call count, and classified steps, in a fixed
order, and nothing measured (durations, tokens, latency, cost). Two runs of the same cells agree on
it or the benchmark is not deterministic.
"""

import hashlib
import json
from collections.abc import Iterable

from mendwork.engine.benchmark.cells import CellResult, cell_key

DIGEST_PREFIX = "sha256:"


def outcomes_digest(cells: Iterable[CellResult]) -> str:
    """The digest of the cells' outcomes, independent of the order they are given in.

    A cell's failure reason is reported but not digested: it can carry text that changes between
    runs of the same cell, such as the port a local server happened to bind. A run that stops
    somewhere new still changes this digest, because its status and its steps change with it.
    """
    content = [
        {
            "cell": cell.cell.model_dump(mode="json"),
            "run_status": cell.run_status,
            "model_calls": cell.model_calls,
            "steps": [step.model_dump(mode="json") for step in cell.steps],
        }
        for cell in sorted(cells, key=lambda item: cell_key(item.cell))
    ]
    text = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return DIGEST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()
