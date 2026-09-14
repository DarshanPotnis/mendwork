"""The rules a model's pick faces that Rung 2's winners do not, applied inside Rung 3."""

from typing import Final

import pytest

from mendwork.adapters.models.fake import FakeModel
from mendwork.engine.domain.heals import AbstentionReason, RejectionReason, RungOutcome
from tests.fakes.browser import FakeBrowser
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button
from tests.unit.healing.rung3_builders import (
    FAR,
    RECORDED,
    changes,
    chooser_for,
    climb_with,
    export_step,
    renamed,
    reply,
    rung3,
)
from tests.unit.replay.builders import browser

pytestmark = pytest.mark.asyncio


async def test_a_pick_from_another_part_of_the_page_is_refused_whatever_its_name() -> None:
    page = browser()
    renamed(page, nearby_text=("Main navigation",))
    model = FakeModel([reply(1)])

    result = await climb_with(page, chooser_for(model))

    assert result.accepted is None
    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    report = rung3(result)
    assert report.outcome is RungOutcome.CHOICE_REFUSED
    [refused] = [c for c in report.candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert refused.rejection.reason is RejectionReason.CONTEXT_LOST
    assert len(model.requests) == 1


async def test_a_pick_that_loses_its_context_while_the_model_chooses_is_refused() -> None:
    page = browser()
    renamed(page)
    model = FakeModel(changes(page, "renamed", nearby_text=("Main navigation",)))

    result = await climb_with(page, chooser_for(model))

    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    [refused] = [c for c in rung3(result).candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert refused.rejection.reason is RejectionReason.CONTEXT_LOST


async def test_a_pick_keeping_part_of_its_context_is_accepted() -> None:
    page = browser()
    renamed(page, nearby_text=("Quarterly ledger", "Updated today"))

    result = await climb_with(page, chooser_for(FakeModel([reply(1)])))

    assert result.accepted is not None
    assert result.accepted.rung == 3


async def test_a_recording_without_nearby_text_has_no_context_to_lose() -> None:
    bare = export_button(selectors=[EXPORT_TEST_ID.model_dump()], nearby_text=[])
    page = browser()
    renamed(page, nearby_text=("Main navigation",))

    result = await climb_with(page, chooser_for(FakeModel([reply(1)])), target=export_step(bare))

    assert result.accepted is not None
    assert result.accepted.rung == 3


async def test_the_context_veto_leaves_a_model_abstention_alone() -> None:
    page = browser()
    renamed(page, nearby_text=("Main navigation",))

    result = await climb_with(page, chooser_for(FakeModel([reply(None)])))

    assert result.abstention is AbstentionReason.MODEL_ABSTAINED
    assert all(c.rejection is None for c in rung3(result).candidates)


def unmarked(page: FakeBrowser) -> None:
    """The export button reworded, with a new id and no test id: it keeps wording, not identity."""
    add_element(
        page,
        "unmarked",
        RECORDED,
        identity={"name": "Ledger download"},
        facts={"text": "Ledger download", "id": "ledger-download", "data_testid": None, "box": FAR},
    )


URL_ONLY: Final = [
    {"kind": "url_matches", "mode": "prefix", "pattern": "https://ledger.example.test/"}
]


async def test_on_a_step_proven_only_by_its_url_a_pick_without_an_identifier_is_refused() -> None:
    page = browser()
    unmarked(page)

    result = await climb_with(
        page, chooser_for(FakeModel([reply(1)])), target=export_step(checkpoints=URL_ONLY)
    )

    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    [refused] = [c for c in rung3(result).candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert refused.rejection.reason is RejectionReason.WEAK_VERIFICATION


async def test_the_same_pick_on_a_step_with_an_effect_checkpoint_is_accepted() -> None:
    page = browser()
    unmarked(page)

    result = await climb_with(page, chooser_for(FakeModel([reply(1)])))

    assert result.accepted is not None
    assert result.accepted.rung == 3


async def test_on_a_step_proven_only_by_its_url_a_pick_keeping_its_test_id_is_accepted() -> None:
    page = browser()
    renamed(page)

    result = await climb_with(
        page, chooser_for(FakeModel([reply(1)])), target=export_step(checkpoints=URL_ONLY)
    )

    assert result.accepted is not None
    assert result.accepted.rung == 3
