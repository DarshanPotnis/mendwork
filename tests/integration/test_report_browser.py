"""A run report in Chromium: it loads nothing, and its outline sits on its screenshot.

The run is the patching unit tests' ledger heal, rendered with real PNG screenshots, so this needs
no portal and stays in the fast suite (ADR 0013).
"""

import pytest
from playwright.async_api import Browser

from mendwork.adapters.report_html.render import render_report
from mendwork.adapters.report_html.writer import load_css
from mendwork.engine.reporting.view import run_report_view
from tests.integration.report_page import load_report
from tests.png import tiny_png
from tests.unit.patching.builders import ledger, ledger_page, ledger_version

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]


async def test_a_report_with_screenshots_and_an_outlined_heal_loads_nothing(
    browser: Browser,
) -> None:
    made = await ledger()
    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher())
    view = run_report_view(run, ledger_version())
    images = {name: tiny_png(320, 180, shade) for shade, name in enumerate(view.images(), start=90)}
    html = render_report(view, images, css=load_css(), budget_bytes=1 << 20, scrub=str)

    loaded = await load_report(browser, html)

    assert (loaded.requests, loaded.policies) == ((), 1)
    assert loaded.images == len(images) == 3
    assert loaded.marks_inside == (True,)
