"""Writing a run's report beside its record (ADR 0013).

The report is rebuilt from ``run.json`` and ``workflow.json`` whenever a run finishes, an approval
resumes it, or a rejection ends it, and written atomically as ``report.html`` in the run's
artifacts. Screenshots are read from the same directory. The stylesheet ships as package data
beside this module.
"""

import asyncio
from collections.abc import Callable
from importlib.resources import files
from typing import Final

from mendwork.adapters.report_html.render import render_report
from mendwork.engine.domain.runs import ArtifactName, Run, RunId
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.replay.artifact_names import REPORT
from mendwork.engine.reporting.view import run_report_view
from mendwork.engine.safety.secret_scrub import SecretScrubber

REPORT_PACKAGE: Final = "mendwork.adapters.report_html"
ArtifactReader = Callable[[RunId, ArtifactName], bytes | None]
"""Reads one of a run's artifacts synchronously; None when it does not exist."""


def load_css() -> str:
    """The report's stylesheet, from the installed package. Blocking."""
    return (files(REPORT_PACKAGE) / "report.css").read_text(encoding="utf-8")


class ReportWriter:
    """Writes run reports into a run's artifacts."""

    def __init__(
        self,
        *,
        artifacts: ArtifactStore,
        read: ArtifactReader,
        scrubber: SecretScrubber,
        budget_bytes: int,
    ) -> None:
        self._artifacts = artifacts
        self._read = read
        self._scrubber = scrubber
        self._budget_bytes = budget_bytes

    async def write(self, run: Run, workflow: WorkflowVersion) -> ArtifactName:
        """Render the run's report and store it as ``report.html``."""
        view = run_report_view(run, workflow)
        images: dict[ArtifactName, bytes] = {}
        for name in view.images():
            data = await asyncio.to_thread(self._read, run.run_id, name)
            if data is not None:
                images[name] = data
        css = await asyncio.to_thread(load_css)
        document = render_report(
            view,
            images,
            css=css,
            budget_bytes=self._budget_bytes,
            scrub=self._scrubber.scrub_text,
        )
        return await self._artifacts.write(run.run_id, REPORT, document.encode("utf-8"))
