"""Rewrite the golden recording of download_report: ``make recording-golden``.

Records the task with the scripted person against a freshly served, unmutated chaos portal,
accepting every proposed name, and writes the canonical YAML. The golden test compares a
new recording with this file, ignoring only what depends on the machine (element boxes,
which follow the platform's fonts) and when it was recorded.
"""

import asyncio
from pathlib import Path
from typing import Final

import structlog
from playwright.async_api import async_playwright

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.portal.server import PortalServer
from tests.integration.portal import PORTAL_ROOT
from tests.integration.recording_harness import record_scripted
from tests.integration.recording_scripts import download_report

GOLDEN: Final = (
    Path(__file__).resolve().parents[1] / "fixtures" / "recordings" / "download_report.yaml"
)


async def recorded_download_report_yaml(portal_url: str) -> bytes:
    """The canonical YAML of a fresh scripted recording of download_report."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            outcome = await record_scripted(browser, f"{portal_url}index.html", download_report)
        finally:
            await browser.close()
    return WorkflowYamlCodec(max_bytes=1 << 20).encode(outcome.workflow("download_report"))


async def main() -> None:
    with PortalServer(PORTAL_ROOT, host="127.0.0.1", port=0) as server:
        data = await recorded_download_report_yaml(server.url)
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_bytes(data)
    structlog.get_logger("mendwork.recording.golden").info("golden_written", path=str(GOLDEN))


if __name__ == "__main__":
    asyncio.run(main())
