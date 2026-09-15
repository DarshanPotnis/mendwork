"""Which version a workflow file runs, and whether its heals may become versions (ADR 0013)."""

from datetime import timedelta
from typing import Final

import pytest

from mendwork.engine.domain.lineage import edit_version, heal_version, roll_back_version
from mendwork.engine.domain.patches import NotSavedReason, WorkflowSource
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.sources import choose_version, first_version
from tests.fakes.clock import FakeClock
from tests.heal_changes import heal_change
from tests.unit.patching.builders import with_intent
from tests.workflows import CREATED_AT_DATETIME, version

PATH: Final = "workflows/demo.yaml"
CLOCK: Final = FakeClock(CREATED_AT_DATETIME + timedelta(days=1))


def lineage() -> tuple[WorkflowVersion, WorkflowVersion, WorkflowVersion]:
    """v1, a heal (v2), and a rollback to v1 (v3)."""
    first = version()
    second = heal_version(first, heal_change(first), clock=CLOCK)
    third = roll_back_version(second, first, reason="wrong", clock=CLOCK)
    return first, second, third


def test_a_first_version_with_an_empty_store_is_saved_and_run_with_its_heals_saved() -> None:
    file = version()

    decision = choose_version(file, PATH, (), exact=False)

    assert (decision.version, decision.publish_first) == (file, True)
    assert decision.source == WorkflowSource(path=PATH, stored_version=1, saves_heals=True)


def test_a_later_version_with_an_empty_store_runs_as_written_without_saving() -> None:
    first = version()
    file = edit_version(first, with_intent(first, "save", "Save it"), summary="e", clock=CLOCK)

    decision = choose_version(file, PATH, (), exact=False)

    assert (decision.version, decision.publish_first) == (file, False)
    assert decision.source.not_saved is NotSavedReason.NO_LINEAGE


def test_the_latest_version_runs_when_every_later_version_came_from_a_heal() -> None:
    first = version()
    second = heal_version(first, heal_change(first), clock=CLOCK)

    decision = choose_version(first, PATH, (first, second), exact=False)

    assert decision.version == second
    assert decision.source == WorkflowSource(
        path=PATH, stored_version=1, ran_stored=True, saves_heals=True
    )


def test_a_file_edited_only_in_comments_or_formatting_is_still_its_version() -> None:
    first = version()
    same_content = first.model_copy(update={"created_at": CREATED_AT_DATETIME + timedelta(hours=3)})

    decision = choose_version(same_content, PATH, (first,), exact=False)

    assert decision.version == first
    assert (decision.source.stored_version, decision.source.saves_heals) == (1, True)


def test_a_rollback_back_to_the_file_s_content_runs_the_rollback() -> None:
    first, second, third = lineage()

    decision = choose_version(first, PATH, (first, second, third), exact=False)

    assert decision.version == third
    assert decision.source.stored_version == 3


def test_a_file_edited_by_hand_runs_as_written_and_saves_nothing() -> None:
    first = version()
    edited = first.model_copy(update={"steps": with_intent(first, "save", "Save the form").steps})

    decision = choose_version(edited, PATH, (first,), exact=False)

    assert decision.version == edited
    assert decision.source.not_saved is NotSavedReason.FILE_DIFFERS


def test_an_older_file_runs_as_written_after_a_later_import() -> None:
    first = version()
    imported = edit_version(
        first, with_intent(first, "save", "Save"), summary="imported", clock=CLOCK
    )

    decision = choose_version(first, PATH, (first, imported), exact=False)

    assert decision.version == first
    assert decision.source == WorkflowSource(
        path=PATH, stored_version=1, saves_heals=False, not_saved=NotSavedReason.NEWER_IMPORT
    )


@pytest.mark.parametrize("stored", [(), "lineage"])
def test_exact_runs_the_file_as_written_whatever_the_store_holds(stored: object) -> None:
    file = version()
    versions = lineage() if stored == "lineage" else ()

    decision = choose_version(file, PATH, versions, exact=True)

    assert (decision.version, decision.publish_first) == (file, False)
    assert decision.source.not_saved is NotSavedReason.EXACT


def test_a_first_version_keeps_the_file_or_is_renumbered_from_it() -> None:
    first = version()
    edited = edit_version(first, with_intent(first, "save", "Save"), summary="e", clock=CLOCK)

    assert first_version(first, CLOCK) is first
    renumbered = first_version(edited, CLOCK)
    assert (renumbered.version, renumbered.change, renumbered.created_at) == (
        1,
        None,
        CLOCK.now(),
    )
    assert renumbered.content == edited.content
