"""Recording a target: selectors verified against the page, scoped when ambiguous, then proven."""

from dataclasses import dataclass

import pytest

from mendwork.engine.domain.enums import SelectorStrategy
from mendwork.engine.domain.recording import DropReason, SelectorChoice
from mendwork.engine.errors import RecordingUnusable
from mendwork.engine.ports.browser_types import ElementIdentity, ElementRef
from mendwork.engine.ports.recording_types import AncestorFacts
from mendwork.engine.recording.selectors import with_scope
from mendwork.engine.recording.targets import TargetRecorder
from tests.fakes.browser import FakeBrowser, FakeElement
from tests.fakes.recording import FakeRecordingBrowser
from tests.unit.recording.builders import (
    DOC,
    add_button,
    browser,
    by_test_id,
    context,
    css,
    facts,
    ref,
    role,
    text,
)

pytestmark = pytest.mark.asyncio


async def pinned(page: FakeRecordingBrowser) -> ElementRef:
    element = await page.pin_capture(ref())
    assert element is not None
    return element


async def reason_of(page: FakeRecordingBrowser) -> str:
    with pytest.raises(RecordingUnusable) as caught:
        await TargetRecorder(context(page)).record(await pinned(page))
    return str(caught.value.context["reason"])


async def test_selectors_that_find_the_element_are_kept_and_the_rest_dropped() -> None:
    page = browser()
    add_button(page, id="save-button", placeholder="unused")
    page.elements["other"] = FakeElement(tag="button", role="button", name="Other")
    page.finds[css("#save-button")] = "other"

    recorded = await TargetRecorder(context(page)).record(await pinned(page))

    assert recorded.fingerprint.selectors == (
        by_test_id("save"),
        role("button", "Save"),
        text("Save"),
    )
    assert [(item.strategy, item.reason, item.level_counts) for item in recorded.dropped] == [
        (SelectorStrategy.PLACEHOLDER, DropReason.NO_MATCH, (0,)),
        (SelectorStrategy.CSS, DropReason.DIFFERENT_ELEMENT, (1,)),
    ]
    assert recorded.choice == SelectorChoice(rank=0, strategy=SelectorStrategy.TEST_ID, total=3)
    assert recorded.fingerprint.accessible_name == "Save"


def view_page() -> FakeRecordingBrowser:
    page = browser()
    page.elements["view"] = FakeElement(tag="button", role="button", name="View order PO-1042")
    page.facts["view"] = facts(text="View order PO-1042", own_text="View")
    page.captured[(DOC, 1)] = "view"
    page.finds[role("button", "View order PO-1042")] = "view"
    page.finds[text("View order PO-1042")] = "view"
    page.finds[role("button", "View", exact=False)] = (12,)
    page.ancestors["view"] = (
        AncestorFacts(tag="td", role="cell", name="View order PO-1042"),
        AncestorFacts(tag="tr", role="row", name="PO-1042 Northwind", row_header="PO-1042"),
        AncestorFacts(tag="tbody", role="rowgroup"),
        AncestorFacts(tag="table", role="table", name="Open orders"),
    )
    return page


async def test_an_ambiguous_candidate_is_scoped_to_its_row() -> None:
    page = view_page()
    scoped = with_scope(role("button", "View", exact=False), role("row", "PO-1042", exact=False))
    assert scoped is not None
    page.finds[scoped] = "view"

    recorded = await TargetRecorder(context(page)).record(await pinned(page))

    assert recorded.fingerprint.selectors == (
        role("button", "View order PO-1042"),
        scoped,
        text("View order PO-1042"),
    )
    assert recorded.dropped == ()


async def test_an_ambiguous_scope_is_scoped_again_up_to_two_levels() -> None:
    page = view_page()
    candidate = role("button", "View", exact=False)
    row = role("row", "PO-1042", exact=False)
    table = role("table", "Open orders")
    first = with_scope(candidate, row)
    nested_row = with_scope(row, table)
    assert first is not None
    assert nested_row is not None
    second = with_scope(candidate, nested_row)
    assert second is not None
    page.finds[first] = (2,)
    page.finds[second] = "view"

    recorded = await TargetRecorder(context(page)).record(await pinned(page))

    assert second in recorded.fingerprint.selectors


async def test_a_candidate_no_scope_makes_unique_is_dropped_as_ambiguous() -> None:
    page = view_page()
    scoped_elsewhere = with_scope(
        role("button", "View", exact=False), role("row", "PO-1042", exact=False)
    )
    assert scoped_elsewhere is not None
    page.elements["other"] = FakeElement(tag="button", role="button", name="View order PO-1043")
    page.finds[scoped_elsewhere] = "other"

    recorded = await TargetRecorder(context(page)).record(await pinned(page))

    assert [(item.reason, item.level_counts) for item in recorded.dropped] == [
        (DropReason.AMBIGUOUS, (12,))
    ]


async def test_no_surviving_selector_ends_the_recording() -> None:
    page = browser()
    add_button(page)
    for candidate in (by_test_id("save"), role("button", "Save"), text("Save")):
        page.finds[candidate] = (2,)

    assert await reason_of(page) == "no_selector"


async def test_an_identity_playwright_does_not_confirm_ends_the_recording() -> None:
    page = browser()
    add_button(page).confirmed = False

    assert await reason_of(page) == "identity_unconfirmed"


async def test_a_page_that_never_stops_changing_ends_the_recording() -> None:
    page = browser(quiet=False, unstable_reads=10_000)
    add_button(page)

    assert await reason_of(page) == "page_never_stable"


async def test_an_element_that_left_the_page_ends_the_recording() -> None:
    page = browser()
    add_button(page)
    page.detached.add("save")

    assert await reason_of(page) == "element_gone"


async def test_a_name_the_format_cannot_store_ends_the_recording() -> None:
    page = browser()
    page.elements["long"] = FakeElement(tag="button", role="button", name="n" * 300)
    page.facts["long"] = facts(data_testid="long")
    page.captured[(DOC, 1)] = "long"
    page.finds[by_test_id("long")] = "long"

    assert await reason_of(page) == "unrecordable_target"


@dataclass
class ChangingElement(FakeElement):
    """An element whose identity reads differently from its second reading on."""

    readings: int = 0
    later_name: str = "Delete data"
    unsettles: FakeBrowser | None = None

    def identity(self, *, confirm: bool) -> ElementIdentity:
        self.readings += 1
        if self.readings > 1 and self.unsettles is not None:
            self.unsettles.change()
        name = self.name if self.readings == 1 or self.unsettles is not None else self.later_name
        return ElementIdentity(
            tag=self.tag, role=self.role, name=name, confirmed=True if confirm else None
        )


async def test_a_drift_found_while_proving_the_step_ends_the_recording() -> None:
    page = browser()
    add_button(page)
    page.elements["save"] = ChangingElement(tag="button", role="button", name="Save")

    assert await reason_of(page) == "identity_unconfirmed"


async def test_a_page_that_changes_while_the_step_is_proven_ends_the_recording() -> None:
    page = browser(quiet=False)
    add_button(page)
    page.elements["save"] = ChangingElement(
        tag="button", role="button", name="Save", unsettles=page
    )

    assert await reason_of(page) == "page_never_stable"
