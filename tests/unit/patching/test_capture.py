"""Capturing a verified heal's element with the recorder's own derivation (ADR 0013)."""

import ast
from pathlib import Path
from typing import Final

import pytest
import structlog
from pydantic import SecretStr

from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.domain.patches import CaptureProblem, FoundTarget, ImageBox
from mendwork.engine.domain.runs import ArtifactName, parse_run_id
from mendwork.engine.errors import RecordingUnusable
from mendwork.engine.patching.capture import HealCapture, capture_problem
from mendwork.engine.recording.target_context import TargetCaptureContext
from mendwork.engine.recording.targets import TargetRecorder
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.evidence import EvidenceRecorder
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser
from tests.fakes.ports import InMemoryArtifactStore
from tests.unit.healing.builders import EXPORT_TEST_ID
from tests.unit.patching.builders import RENAMED, ledger_page

RUN: Final = parse_run_id("20260915T120000Z-00000001")
EXPORT_STEP: Final = StepId("export")
ENGINE: Final = Path(__file__).resolve().parents[3] / "src" / "mendwork" / "engine"
DERIVATION: Final = frozenset(
    {"candidate_selectors", "scope_selectors", "with_scope", "build_fingerprint"}
)


def targets(page: FakeBrowser, scrubber: SecretScrubber) -> TargetCaptureContext:
    return TargetCaptureContext(
        browser=page,
        timer=page.timer,
        scrubber=scrubber,
        step_timeout_ms=2_000,
        settle_timeout_ms=100,
        settle_quiet_frames=2,
        scope_ancestors_max=6,
    )


def capture_for(
    page: FakeBrowser, scrubber: SecretScrubber | None = None
) -> tuple[HealCapture, InMemoryArtifactStore]:
    scrubber = scrubber or SecretScrubber()
    artifacts = InMemoryArtifactStore()
    log = structlog.stdlib.get_logger("test")
    evidence = EvidenceRecorder(
        browser=page, artifacts=artifacts, run_id=RUN, scrubber=scrubber, timeout_ms=1_000, log=log
    )
    return HealCapture(targets=targets(page, scrubber), evidence=evidence, log=log), artifacts


async def captured(healer: HealCapture, page: FakeBrowser) -> FoundTarget:
    """The export button, captured as the second step's healed element."""
    return await healer.capture(
        1, EXPORT_STEP, page.pin("export"), Deadline.after(page.timer, 10_000)
    )


@pytest.mark.asyncio
async def test_a_healed_element_is_fingerprinted_exactly_as_the_recorder_would_record_it() -> None:
    page = ledger_page()
    healer, artifacts = capture_for(page)

    found = await captured(healer, page)

    assert found.problem is None
    assert found.fingerprint is not None
    assert (found.fingerprint.accessible_name, found.fingerprint.selectors) == (
        RENAMED,
        (EXPORT_TEST_ID,),
    )
    recorded = await TargetRecorder(targets(page, SecretScrubber())).record(page.pin("export"))
    assert found.fingerprint == recorded.fingerprint
    assert found.screenshot == ArtifactName("steps/002_export.found.png")
    assert found.box == ImageBox(x=0.4, y=0.3, width=0.1, height=0.05)
    assert artifacts.files[(RUN, ArtifactName("steps/002_export.found.png"))] == b"\x89PNG view"
    assert page.views == ["export"]


@pytest.mark.asyncio
async def test_an_element_no_selector_finds_alone_has_no_fingerprint_but_keeps_its_view() -> None:
    page = ledger_page()
    del page.finds[EXPORT_TEST_ID]
    healer, _ = capture_for(page)

    found = await captured(healer, page)

    assert (found.fingerprint, found.problem) == (None, CaptureProblem.NO_SELECTOR)
    assert found.detail is not None
    assert found.screenshot is not None


@pytest.mark.asyncio
async def test_an_element_whose_identity_playwright_cannot_confirm_is_not_fingerprinted() -> None:
    page = ledger_page()
    page.elements["export"].confirmed = False
    healer, _ = capture_for(page)

    found = await captured(healer, page)

    assert found.problem is CaptureProblem.IDENTITY_UNCONFIRMED


@pytest.mark.asyncio
async def test_an_element_whose_text_holds_a_secret_is_never_stored() -> None:
    page = ledger_page()
    scrubber = SecretScrubber()
    scrubber.register(SecretStr("Share ledger"))
    healer, _ = capture_for(page, scrubber)

    found = await captured(healer, page)

    assert (found.fingerprint, found.problem) == (None, CaptureProblem.SECRET_IN_TARGET)


@pytest.mark.asyncio
async def test_an_element_that_left_the_page_has_neither_view_nor_fingerprint() -> None:
    page = ledger_page()
    page.detached.add("export")
    healer, _ = capture_for(page)

    found = await captured(healer, page)

    assert (found.problem, found.screenshot, found.box) == (CaptureProblem.ELEMENT_GONE, None, None)


def test_a_recording_failure_the_capture_does_not_know_is_an_unrecordable_target() -> None:
    assert capture_problem(RecordingUnusable("x", reason="protocol_violation")) is (
        CaptureProblem.UNRECORDABLE_TARGET
    )
    assert capture_problem(RecordingUnusable("x", reason="nonsense")) is (
        CaptureProblem.UNRECORDABLE_TARGET
    )


def test_selectors_are_derived_only_by_the_recorder() -> None:
    offenders: list[str] = []
    for package in ("patching", "replay", "reporting"):
        for path in sorted((ENGINE / package).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names = {alias.name for alias in node.names} & DERIVATION
                    if names:
                        offenders.append(f"{path.name}: {sorted(names)}")

    assert offenders == []
