"""A process that stops between publishing a version and recording it (ADR 0013's ordering).

A child process runs the renamed-ledger heal against the real file adapters and ends itself the
instant the version is published: before the pending patches are replaced and before the run's final
record is written. The store must hold a valid version, the run's record must still be its journal
(running, no patch outcome), and the next runs must reconcile: neither publishing the heal a second
time nor losing it.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.patches import PatchResult
from mendwork.engine.domain.runs import Run, RunStatus
from mendwork.engine.domain.workflow import WorkflowVersion
from tests.heal_changes import target_of
from tests.unit.patching.builders import RENAMED, WORKFLOW_ID, ledger_version, result
from tests.unit.patching.crash_child import KILLED, patched_run, run_id_for, stores

REPO: Final = Path(__file__).resolve().parents[3]


def stopped_after_publishing(root: Path, policy: str, number: int) -> None:
    """Run the child, which must end itself right after publishing."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("COV_CORE_", "COVERAGE_"))
    }
    completed = subprocess.run(  # noqa: S603 - a fixed command line on our own interpreter
        [sys.executable, "-m", "tests.unit.patching.crash_child", str(root), policy, str(number)],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == KILLED, completed.stdout[-2000:] + completed.stderr[-2000:]


def stored(root: Path) -> tuple[tuple[int, ...], WorkflowVersion | None]:
    store, _, _ = stores(root)
    versions = asyncio.run(store.versions(WORKFLOW_ID))
    return versions, asyncio.run(store.latest(WORKFLOW_ID))


def journal(root: Path, number: int) -> Run:
    path = root / "artifacts" / "runs" / run_id_for(number) / "run.json"
    return Run.model_validate_json(path.read_bytes())


def test_a_process_that_stops_after_publishing_leaves_a_valid_version_nothing_duplicates(
    tmp_path: Path,
) -> None:
    stopped_after_publishing(tmp_path, "immediate", 1)

    versions, latest = stored(tmp_path)
    assert versions == (1, 2)
    assert latest is not None
    assert isinstance(latest.change, HealChange)
    assert target_of(latest, "export").accessible_name == RENAMED
    assert sorted(path.name for path in (tmp_path / "store" / "ledger").iterdir()) == [
        "v0001.yaml",
        "v0002.yaml",
    ]
    left = journal(tmp_path, 1)
    assert (left.status, left.patches) == (RunStatus.RUNNING, ())

    rerun = asyncio.run(patched_run(tmp_path, latest, policy="immediate", number=2))
    assert (rerun.status, result(rerun, "export").heal, rerun.patches) == (
        RunStatus.SUCCEEDED,
        None,
        (),
    )

    older = asyncio.run(patched_run(tmp_path, ledger_version(), policy="immediate", number=3))
    assert [(item.result, item.version) for item in older.patches] == [
        (PatchResult.ALREADY_APPLIED, 2)
    ]
    assert stored(tmp_path)[0] == (1, 2)


def test_a_process_that_stops_before_replacing_pending_patches_is_reconciled_by_the_next_run(
    tmp_path: Path,
) -> None:
    first = asyncio.run(patched_run(tmp_path, ledger_version(), policy="after_n", number=1))
    assert [(item.result, item.successes) for item in first.patches] == [(PatchResult.PENDING, 1)]

    stopped_after_publishing(tmp_path, "after_n", 2)

    versions, latest = stored(tmp_path)
    assert versions == (1, 2)
    assert latest is not None
    _, pending, _ = stores(tmp_path)
    [left_behind] = asyncio.run(pending.read(WORKFLOW_ID))
    assert len(left_behind.successes) == 1
    left = journal(tmp_path, 2)
    assert (left.status, left.patches) == (RunStatus.RUNNING, ())

    third = asyncio.run(patched_run(tmp_path, latest, policy="after_n", number=3))

    assert result(third, "export").heal is None
    assert [(item.result, item.detail) for item in third.patches] == [
        (PatchResult.DISCARDED, "v2 already targets this element")
    ]
    assert asyncio.run(pending.read(WORKFLOW_ID)) == ()
    assert stored(tmp_path)[0] == (1, 2)
