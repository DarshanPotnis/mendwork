"""Audit entries: a chain that breaks when any entry is changed, removed, or reordered."""

import string

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from mendwork.engine.domain.audit import (
    GENESIS_SHA256,
    AuditDraft,
    AuditEntry,
    AuditKind,
    chain_problem,
    chained,
)
from tests.unit.replay.approval_builders import entry

REASONS = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;:!?'-éü€", min_size=1, max_size=500
).filter(lambda text: bool(text.strip()))


def chain(count: int) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    for sequence in range(1, count + 1):
        previous = entries[-1].sha256 if entries else GENESIS_SHA256
        entries.append(entry(sequence=sequence, previous=previous, reason=f"reason {sequence}"))
    return entries


def test_entries_chained_in_order_have_no_problem() -> None:
    entries = chain(3)

    assert chain_problem(entries) is None
    assert entries[0].previous_sha256 == GENESIS_SHA256
    assert [item.previous_sha256 for item in entries[1:]] == [item.sha256 for item in entries[:-1]]


def test_an_entry_changed_after_it_was_recorded_breaks_the_chain() -> None:
    entries = chain(3)
    entries[1] = entries[1].model_copy(update={"reason": "something else"})

    assert chain_problem(entries) == "entry 2 was changed after it was recorded"


def test_a_removed_entry_breaks_the_chain() -> None:
    entries = chain(3)

    assert chain_problem([entries[0], entries[2]]) == "entry 2 is numbered 3"


def test_reordered_entries_break_the_chain() -> None:
    first, second = chain(2)
    renumbered = [
        second.model_copy(update={"sequence": 1}),
        first.model_copy(update={"sequence": 2}),
    ]

    assert chain_problem(renumbered) == "entry 1 does not follow the entry before it"


@given(REASONS)
def test_an_entry_survives_a_json_round_trip_with_its_chain_intact(reason: str) -> None:
    recorded = entry(reason=reason)

    again = AuditEntry.model_validate_json(recorded.model_dump_json())

    assert again == recorded
    assert chain_problem([again]) is None


@pytest.mark.parametrize("reason", ["", "   ", "two\nlines", "x" * 501])
def test_a_reason_is_one_line_of_at_most_500_characters(reason: str) -> None:
    with pytest.raises(ValidationError):
        AuditDraft.model_validate({**entry().model_dump(), "reason": reason})


def test_chaining_numbers_the_entry_and_links_it_to_the_one_before() -> None:
    draft = AuditDraft.model_validate(
        entry(AuditKind.PROPOSAL_REJECTED).model_dump(
            exclude={"audit_version", "sequence", "previous_sha256", "sha256"}
        )
    )

    linked = chained(draft, sequence=7, previous_sha256="b" * 64)

    assert (linked.sequence, linked.previous_sha256, linked.audit_version) == (7, "b" * 64, 1)
    assert linked.kind is AuditKind.PROPOSAL_REJECTED
