"""The file ledger counts each day's model calls exactly, across threads and processes, and fails
closed."""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from mendwork.adapters.usage_fs.ledger import FileUsageLedger
from mendwork.engine.errors import BudgetExceeded

pytestmark = pytest.mark.asyncio

DAY: Final = date(2026, 9, 13)
REPO: Final = Path(__file__).resolve().parents[3]
_RESERVE_IN_ANOTHER_PROCESS: Final = """
import asyncio, sys
from datetime import date
from pathlib import Path
from mendwork.adapters.usage_fs.ledger import FileUsageLedger

async def main() -> None:
    ledger = FileUsageLedger(Path(sys.argv[1]))
    granted = [await ledger.reserve_call(date(2026, 9, 13), 12) for _ in range(5)]
    print(sum(1 for count in granted if count is not None))

asyncio.run(main())
"""


async def test_calls_are_counted_per_day_until_the_limit(tmp_path: Path) -> None:
    ledger = FileUsageLedger(tmp_path / "usage")

    granted = [await ledger.reserve_call(DAY, 2) for _ in range(3)]
    other_day = await ledger.reserve_call(date(2026, 9, 14), 2)

    assert granted == [1, 2, None]
    assert other_day == 1
    assert json.loads(ledger.document(DAY).read_text(encoding="utf-8")) == {
        "calls": 2,
        "day": "2026-09-13",
        "ledger_version": 1,
    }
    assert not list((tmp_path / "usage").glob("*.tmp"))


async def test_a_limit_of_zero_allows_nothing_and_writes_nothing(tmp_path: Path) -> None:
    ledger = FileUsageLedger(tmp_path / "usage")

    assert await ledger.reserve_call(DAY, 0) is None
    assert not (tmp_path / "usage").exists()


async def test_concurrent_reservations_never_count_the_same_call_twice(tmp_path: Path) -> None:
    ledger = FileUsageLedger(tmp_path)

    granted = await asyncio.gather(*(ledger.reserve_call(DAY, 10) for _ in range(20)))

    assert sorted(count for count in granted if count is not None) == list(range(1, 11))
    assert granted.count(None) == 10


async def test_processes_share_one_count(tmp_path: Path) -> None:
    processes = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _RESERVE_IN_ANOTHER_PROCESS,
            str(tmp_path),
            cwd=REPO,
            stdout=asyncio.subprocess.PIPE,
        )
        for _ in range(4)
    ]
    outputs = [await process.communicate() for process in processes]

    assert sum(int(stdout) for stdout, _ in outputs) == 12
    assert (
        json.loads(FileUsageLedger(tmp_path).document(DAY).read_text(encoding="utf-8"))["calls"]
        == 12
    )


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"ledger_version": 2, "day": "2026-09-13", "calls": 1}',
        '{"ledger_version": 1, "day": "2026-09-12", "calls": 1}',
        '{"ledger_version": 1, "day": "2026-09-13", "calls": -1}',
        '{"ledger_version": 1, "day": "2026-09-13", "calls": true}',
        "[]",
    ],
)
async def test_a_document_the_ledger_did_not_write_allows_no_call(
    tmp_path: Path, content: str
) -> None:
    ledger = FileUsageLedger(tmp_path)
    ledger.document(DAY).write_text(content, encoding="utf-8")

    with pytest.raises(BudgetExceeded) as caught:
        await ledger.reserve_call(DAY, 200)

    assert caught.value.context["reason"] == "ledger_unavailable"
    assert caught.value.context["scope"] == "day"


async def test_a_directory_that_cannot_be_used_allows_no_call(tmp_path: Path) -> None:
    blocked = tmp_path / "usage"
    blocked.write_text("a file where the directory should be", encoding="utf-8")

    with pytest.raises(BudgetExceeded, match="could not be used"):
        await FileUsageLedger(blocked).reserve_call(DAY, 200)
