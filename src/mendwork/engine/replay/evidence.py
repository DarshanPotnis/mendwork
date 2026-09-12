"""Capturing a run's evidence: screenshots, DOM snapshots, traces, and downloads.

Evidence of a failure must never replace the failure itself, so a capture that fails
while recording a failed step is noted on the step (``capture_errors``) and logged. A
store that cannot write while a step succeeds is different: the run cannot keep its
record, so that error propagates.
"""

import structlog

from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.runs import (
    ArtifactName,
    RunId,
    TraceWithheld,
    TraceWithheldReason,
)
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import ArtifactStoreUnavailable, MendworkError
from mendwork.engine.ports.artifacts import ArtifactStore
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.browser_types import (
    DownloadObservation,
    TraceDisabled,
    TraceNotSaved,
    TraceSaved,
)
from mendwork.engine.replay.artifact_names import (
    TRACE,
    dom_snapshot_name,
    download_name,
    screenshot_name,
)
from mendwork.engine.safety.secret_scrub import SecretScrubber


class EvidenceRecorder:
    """Stores one run's evidence and remembers what must be masked or withheld."""

    def __init__(
        self,
        *,
        browser: BrowserPort,
        artifacts: ArtifactStore,
        run_id: RunId,
        scrubber: SecretScrubber,
        timeout_ms: int,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        self._browser = browser
        self._artifacts = artifacts
        self._run_id = run_id
        self._scrubber = scrubber
        self._timeout_ms = timeout_ms
        self._log = log
        self._masks: list[Selector] = []
        self._secret_typed_at: tuple[int, StepId] | None = None
        self._downloads: set[str] = set()

    def secret_typed(self, index: int, step_id: StepId, selector: Selector) -> None:
        """Note that a step typed a secret into the field this selector found.

        Every later screenshot masks that field, and a withheld trace names this step.
        """
        if selector not in self._masks:
            self._masks.append(selector)
        self._secret_typed_at = (index, step_id)

    async def screenshot(
        self, index: int, step_id: StepId, problems: list[str], *, fatal: bool
    ) -> ArtifactName | None:
        """A screenshot of the page as the step ends."""
        try:
            data = await self._browser.screenshot(
                mask=tuple(self._masks), timeout_ms=self._timeout_ms
            )
            return await self._artifacts.write(self._run_id, screenshot_name(index, step_id), data)
        except ArtifactStoreUnavailable as error:
            if fatal:
                raise
            self._note(problems, "screenshot", error)
        except MendworkError as error:
            self._note(problems, "screenshot", error)
        return None

    async def dom_snapshot(
        self, index: int, step_id: StepId, problems: list[str]
    ) -> ArtifactName | None:
        """The DOM of a failed step's page, scrubbed of secrets."""
        try:
            html = self._scrubber.scrub_text(await self._browser.dom_snapshot())
            return await self._artifacts.write(
                self._run_id, dom_snapshot_name(index, step_id), html.encode("utf-8")
            )
        except MendworkError as error:
            self._note(problems, "dom snapshot", error)
        return None

    async def trace(self, problems: list[str]) -> tuple[ArtifactName | None, TraceWithheld | None]:
        """A failed step's trace, or the reason it was withheld."""
        try:
            export = await self._browser.export_trace(scrubber=self._scrubber)
            match export:
                case TraceSaved():
                    return await self._artifacts.adopt(self._run_id, TRACE, export.path), None
                case TraceNotSaved():
                    return None, self._withheld(export.reason)
                case TraceDisabled():
                    return None, None
        except MendworkError as error:
            self._note(problems, "trace", error)
        return None, None

    async def keep_download(
        self, download: DownloadObservation, index: int, step_id: StepId
    ) -> ArtifactName:
        """Move a completed download into the run's artifacts."""
        if download.path is None:
            raise MendworkError("only a completed download can be kept")
        name = download_name(download.suggested_filename, index, step_id, self._downloads)
        self._downloads.add(name)
        return await self._artifacts.adopt(self._run_id, name, download.path)

    def _withheld(self, reason: TraceWithheldReason) -> TraceWithheld:
        typed_at = self._secret_typed_at
        if reason is TraceWithheldReason.SECRET_BEARING_PAGE and typed_at is not None:
            return TraceWithheld(
                reason=reason, typed_at_index=typed_at[0], typed_at_step=typed_at[1]
            )
        return TraceWithheld(reason=reason)

    def _note(self, problems: list[str], what: str, error: MendworkError) -> None:
        message = self._scrubber.scrub_text(error.message)
        problems.append(f"{what}: {type(error).__name__}: {message}")
        self._log.warning("evidence_not_captured", evidence=what, error_type=type(error).__name__)
