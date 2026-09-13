"""The candidate scan in Chromium: visible, action-compatible elements, counted, never read.

Fixture pages only, set with ``page.set_content``. The scan's page script finds elements; the
identity and facts scripts describe them, so these tests also prove the three work together.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import pytest
from playwright.async_api import Browser

from mendwork.adapters.browser_playwright.launcher import SessionOptions, open_session
from mendwork.adapters.browser_playwright.scripts import PageScripts
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.runs import parse_run_id
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.ports.candidate_types import CandidateQuery, CandidateScan
from tests.unit.replay.builders import selector

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

OPTIONS: Final = SessionOptions(
    viewport_width=1280, viewport_height=720, default_timeout_ms=5_000, trace_on_failure=False
)
PAGE: Final = """
<main>
  <h2>Quarterly ledger</h2>
  <button id="export" data-testid="ledger-export">Export ledger</button>
  <button style="display: none">Hidden</button>
  <button style="visibility: hidden">Invisible</button>
  <button style="width: 0; height: 0; padding: 0; border: 0"></button>
  <div inert><button>Inert</button></div>
  <a>No destination</a>
  <a href="/ledger/history">History</a>
  <div role="button" tabindex="0">Archive view</div>
  <label for="reference">Customer reference</label>
  <input id="reference" name="reference">
  <textarea aria-label="Notes"></textarea>
  <input type="hidden" name="token" value="t">
  <select aria-label="Currency"><option>EUR</option></select>
</main>
"""
SECRET: Final = "Vault-Passcode-7731!"


@asynccontextmanager
async def session_on(browser: Browser, html: str) -> AsyncIterator[PlaywrightSession]:
    scripts = await asyncio.to_thread(PageScripts.load)
    run_id = parse_run_id("20260913T000000Z-0000cafe")
    async with open_session(browser, scripts, OPTIONS, run_id) as session:
        await session.page.set_content(html)
        yield session


def names(scan: CandidateScan) -> list[str]:
    return [candidate.identity.name for candidate in scan.candidates]


async def test_a_click_scan_finds_visible_activatable_elements_in_document_order(
    browser: Browser,
) -> None:
    async with session_on(browser, PAGE) as session:
        scan = await session.scan_candidates(CandidateQuery(action=ActionType.CLICK, limit=100))

        assert names(scan) == [
            "Export ledger",
            "History",
            "Archive view",
            "Customer reference",
            "Notes",
            "Currency",
        ]
        assert (scan.total, scan.capped) == (6, False)
        export = scan.candidates[0]
        assert (export.identity.role, export.facts.data_testid, export.facts.id) == (
            "button",
            "ledger-export",
            "export",
        )
        assert export.facts.nearby_text == ("Quarterly ledger",)
        assert export.identity.confirmed is None
        await session.release([candidate.element for candidate in scan.candidates])


async def test_fill_and_select_scans_find_only_what_those_actions_can_use(
    browser: Browser,
) -> None:
    async with session_on(browser, PAGE) as session:
        fill = await session.scan_candidates(CandidateQuery(action=ActionType.FILL, limit=100))
        choose = await session.scan_candidates(CandidateQuery(action=ActionType.SELECT, limit=100))
        navigate = await session.scan_candidates(
            CandidateQuery(action=ActionType.NAVIGATE, limit=100)
        )

        assert names(fill) == ["Customer reference", "Notes"]
        assert fill.candidates[0].facts.label_text == "Customer reference"
        assert names(choose) == ["Currency"]
        assert (navigate.total, navigate.candidates) == (0, ())


async def test_a_scan_pins_at_most_its_limit_and_counts_every_candidate(browser: Browser) -> None:
    buttons = "".join(f"<button>Row {number}</button>" for number in range(5))

    async with session_on(browser, f"<main>{buttons}</main>") as session:
        scan = await session.scan_candidates(CandidateQuery(action=ActionType.CLICK, limit=2))

        assert names(scan) == ["Row 0", "Row 1"]
        assert (scan.total, scan.capped) == (5, True)


async def test_a_scan_never_reads_what_a_field_holds(browser: Browser) -> None:
    html = '<form><label for="code">Vault passcode</label><input id="code" type="password"></form>'

    async with session_on(browser, html) as session:
        await session.page.fill("#code", SECRET)
        scan = await session.scan_candidates(CandidateQuery(action=ActionType.FILL, limit=10))

        [field] = scan.candidates
        assert field.facts.masked
        assert SECRET not in scan.model_dump_json()


async def test_facts_of_an_element_whose_document_was_replaced_are_not_found(
    browser: Browser,
) -> None:
    async with session_on(browser, PAGE) as session:
        match = await session.resolve_unique(selector(strategy="test_id", value="ledger-export"))
        assert match.element is not None
        facts = await session.element_facts(match.element)
        assert facts.text == "Export ledger"

        await session.page.goto("about:blank")

        with pytest.raises(TargetNotFound):
            await session.element_facts(match.element)
