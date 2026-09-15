"""Writing a run's HTML report from the commands that end runs (ADR 0013).

A report is evidence, not an outcome: when one cannot be written the command says why on stderr and
exits with the run's code all the same.
"""

from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.report_html.writer import ReportWriter
from mendwork.engine.domain.runs import Run, RunId
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import InfrastructureError
from mendwork.engine.replay.artifact_names import WORKFLOW_SNAPSHOT
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings

_NOT_WRITTEN = "The run report was not written"


async def write_report(
    artifacts: LocalArtifactStore,
    run: Run,
    workflow: WorkflowVersion | None,
    *,
    settings: Settings,
    scrubber: SecretScrubber,
    stderr: TextIO,
) -> Path | None:
    """Write the run's report and return its path; None, with the reason on stderr, when it failed.

    ``scrubber`` is the process's, which holds every secret the command resolved.
    """
    if workflow is None:
        stderr.write(f"{_NOT_WRITTEN}: the run's workflow snapshot is missing or unreadable.\n")
        return None
    writer = ReportWriter(
        artifacts=artifacts,
        read=artifacts.read_now,
        scrubber=scrubber,
        budget_bytes=settings.report_screenshots_max_bytes,
    )
    try:
        name = await writer.write(run, workflow)
    except InfrastructureError as error:
        stderr.write(f"{_NOT_WRITTEN}: {type(error).__name__}: {error.message}\n")
        return None
    except OSError as error:
        stderr.write(f"{_NOT_WRITTEN}: {type(error).__name__}: {error}\n")
        return None
    return artifacts.run_directory(run.run_id) / name


def saved_workflow(artifacts: LocalArtifactStore, run_id: RunId) -> WorkflowVersion | None:
    """The workflow a run executed, from its snapshot; None when it is missing or unreadable."""
    try:
        data = artifacts.read_now(run_id, WORKFLOW_SNAPSHOT)
    except InfrastructureError:
        return None
    if data is None:
        return None
    try:
        return WorkflowVersion.model_validate_json(data)
    except ValidationError:
        return None
