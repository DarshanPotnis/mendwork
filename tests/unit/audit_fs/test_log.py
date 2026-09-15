"""The file audit log: append-only, chained, private, and closed to a log it cannot trust."""

import asyncio
import stat
from pathlib import Path

import pytest

from mendwork.adapters.audit_fs.log import LOG_NAME, FileAuditLog
from mendwork.engine.domain.audit import AuditDraft, AuditKind, chain_problem
from mendwork.engine.domain.runs import parse_run_id
from mendwork.engine.errors import AuditLogCorrupt
from tests.unit.replay.approval_builders import RUN_ID, entry

pytestmark = pytest.mark.asyncio

OTHER_RUN = parse_run_id("20260914T101500Z-0000ffff")


def draft(**overrides: object) -> AuditDraft:
    fields = entry().model_dump(exclude={"audit_version", "sequence", "previous_sha256", "sha256"})
    return AuditDraft.model_validate({**fields, **overrides})


async def test_entries_are_appended_in_order_and_read_back_per_run(tmp_path: Path) -> None:
    log = FileAuditLog(tmp_path / "audit")

    first = await log.append(draft())
    second = await log.append(draft(run_id=OTHER_RUN, kind=AuditKind.PROPOSAL_REJECTED))
    third = await log.append(draft(reason="checked with finance"))

    assert [item.sequence for item in (first, second, third)] == [1, 2, 3]
    assert await log.entries_for_run(RUN_ID) == (first, third)
    assert await log.entries_for_run(OTHER_RUN) == (second,)
    assert len(log.path.read_bytes().splitlines()) == 3


async def test_the_log_file_is_readable_by_its_owner_only(tmp_path: Path) -> None:
    log = FileAuditLog(tmp_path / "audit")

    await log.append(draft())

    assert log.path.name == LOG_NAME
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600


async def test_a_log_that_does_not_exist_yet_has_no_entries(tmp_path: Path) -> None:
    assert await FileAuditLog(tmp_path / "audit").entries_for_run(RUN_ID) == ()


async def test_concurrent_appends_never_take_the_same_place(tmp_path: Path) -> None:
    log = FileAuditLog(tmp_path / "audit")

    appended = await asyncio.gather(*(log.append(draft()) for _ in range(12)))

    assert sorted(item.sequence for item in appended) == list(range(1, 13))
    assert chain_problem(await log.entries_for_run(RUN_ID)) is None


@pytest.mark.parametrize(
    ("damage", "problem"),
    [
        (lambda data: data[:-1], "ends with a line cut short"),
        (lambda data: data + b"not an entry\n", "line 3 of the audit log is not an audit entry"),
        (
            lambda data: data.replace(b'"reason":"first"', b'"reason":"edited"'),
            "entry 1 was changed after it was recorded",
        ),
    ],
)
async def test_a_damaged_log_refuses_every_read_and_append(
    tmp_path: Path, damage: object, problem: str
) -> None:
    log = FileAuditLog(tmp_path / "audit")
    await log.append(draft(reason="first"))
    await log.append(draft(reason="second"))
    assert callable(damage)
    log.path.write_bytes(damage(log.path.read_bytes()))
    before = log.path.read_bytes()

    with pytest.raises(AuditLogCorrupt, match=problem):
        await log.entries_for_run(RUN_ID)
    with pytest.raises(AuditLogCorrupt, match=problem):
        await log.append(draft(reason="third"))
    assert log.path.read_bytes() == before


async def test_a_log_that_cannot_be_opened_is_reported_as_unusable(tmp_path: Path) -> None:
    blocked = tmp_path / "audit"
    blocked.write_text("a file where the directory should be", encoding="utf-8")
    unreadable = FileAuditLog(tmp_path / "elsewhere")
    unreadable.path.mkdir(parents=True)

    with pytest.raises(AuditLogCorrupt, match="could not be written"):
        await FileAuditLog(blocked).append(draft())
    with pytest.raises(AuditLogCorrupt, match="could not be read"):
        await unreadable.entries_for_run(RUN_ID)
