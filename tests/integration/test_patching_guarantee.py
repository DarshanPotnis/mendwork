"""The patching guarantee end to end (ADR 0013): a verified heal becomes a new version, and a rerun
on the same page needs no heal and no model call.

Every run replays download_report in-process, in real Chromium against the chaos portal, with every
action checked against ground truth, through the same source resolution and Patcher as
``mendwork run``: the committed example file is matched by content to the test's own workflow store.

- **Level 3 seed 3.** Rung 2 declines ``open_reports``; the ground-truth model chooses its target at
  Rung 3, the run succeeds, and v2 is published. Rerunning the same file on the same seed runs v2,
  and every way a heal or a model call could show is counted: no step has a heal report, no
  candidate was scanned, no pending patch was tried first, the run's model usage is zero, the model
  was never asked, and the model-call ledger still holds the first run's one call.
- **Page states v2 no longer matches.** On level 0 seed 0, and on the rule seed, v2's model-chosen
  target is not found as healed. The rule seed is the smallest level-3 seed other than 3 whose chaos
  mutates the step's target, by the portal's own ground truth; a test pins that it is seed 2. Both
  heal again at Rung 2 and publish v3, and no action reaches a wrong element.
- **after_n_successes, N = 2**, on level 3 seed 0: the first run's Rung 2 heal waits as a pending
  patch; the second run tries it first, needs no heal, and publishes v2; the third runs v2 with no
  heal and no first try.
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
import pytest_asyncio
from playwright.async_api import Browser

from benchmarks.chaos.heal_cases import (
    USAGE_DIRECTORY,
    GroundTruthRun,
    load_workflows,
    run_with_ground_truth,
)
from benchmarks.chaos.models import Asked, ModelMode
from benchmarks.chaos.rung0_seeds import ABSTAIN_EXPECTED, DEMO_EMAIL, ground_truth
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.report_html.writer import ReportWriter
from mendwork.adapters.storage_fs.pending_patches import FilePendingPatches
from mendwork.adapters.storage_fs.workflow_store import FileWorkflowStore
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.changes import HealChange
from mendwork.engine.domain.enums import PromotionPolicy, VerificationStrength
from mendwork.engine.domain.identifiers import WorkflowId
from mendwork.engine.domain.patches import PatchResult
from mendwork.engine.domain.runs import Run, RunStatus
from mendwork.engine.domain.steps import step_target
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.config import PatchingConfig
from mendwork.engine.patching.patcher import Patcher
from mendwork.engine.patching.sources import SourceDecision, resolve_source, stored_history
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.integration.portal import DEMO_PASSWORD
from tests.integration.replay_harness import replay_settings
from tests.integration.report_page import load_report
from tests.workflows import example_path

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

WORKFLOW: Final = WorkflowId("download_report")
FILE: Final = str(example_path(WORKFLOW))
CODEC: Final = WorkflowYamlCodec(max_bytes=1 << 20)
HEALING_SEED: Final = 3
RULE_SEED: Final = 2


@dataclass(frozen=True, slots=True)
class Lineage:
    """The test's own workflow store, and the directory its runs write to."""

    directory: Path
    policy: PromotionPolicy

    @property
    def store(self) -> FileWorkflowStore:
        return FileWorkflowStore(self.directory / "store", CODEC)

    @property
    def pending(self) -> FilePendingPatches:
        return FilePendingPatches(self.directory / "store")

    def patcher(self) -> Patcher:
        config = PatchingConfig(promotion=self.policy, successes_required=2, publish_attempts=5)
        return Patcher(store=self.store, pending=self.pending, clock=SystemClock(), config=config)

    def copied_to(self, directory: Path) -> "Lineage":
        """The same stored versions under another directory, promoting immediately."""
        shutil.copytree(self.directory / "store", directory / "store")
        return Lineage(directory, PromotionPolicy.IMMEDIATE)

    async def versions(self) -> tuple[WorkflowVersion, ...]:
        return await stored_history(self.store, WORKFLOW)

    def ledger_calls(self) -> int:
        """Model calls the runs' daily ledger holds, over every day it has a document for."""
        usage = self.directory / "runs" / USAGE_DIRECTORY
        return sum(
            int(json.loads(path.read_text(encoding="utf-8"))["calls"])
            for path in usage.glob("model-calls-*.json")
        )


@dataclass(frozen=True, slots=True)
class Ran:
    """One run of the example file: the version it ran, and what it did."""

    decision: SourceDecision
    replay: GroundTruthRun
    asked: tuple[Asked, ...]

    @property
    def run(self) -> Run:
        return self.replay.run

    def healed(self) -> list[tuple[str, int | None]]:
        return [(step.step_id, step.heal.healed_rung) for step in self.run.steps if step.heal]

    def first_tries(self) -> list[str]:
        return [
            step.step_id
            for step in self.run.steps
            if step.target is not None and step.target.pending_patch is not None
        ]

    def patches(self) -> list[tuple[str, PatchResult, int | None]]:
        return [(item.step_id, item.result, item.version) for item in self.run.patches]


async def run_file(
    browser: Browser, portal_url: str, lineage: Lineage, *, level: int, seed: int, model: ModelMode
) -> Ran:
    """Run the example file as ``mendwork run`` would, on one chaos seed and level."""
    workflow = load_workflows()[WORKFLOW]
    applied = await ground_truth(browser, portal_url, seed, level)
    abstain_keys = {
        mutation.target_key
        for mutations in applied.values()
        for mutation in mutations
        if mutation.category == ABSTAIN_EXPECTED and mutation.target_key is not None
    }
    decision = await resolve_source(lineage.store, workflow.version, FILE, exact=False)
    asked: list[Asked] = []
    replay = await run_with_ground_truth(
        browser,
        decision.version,
        {
            "portal_url": f"{portal_url}index.html?seed={seed}&level={level}",
            "account_email": DEMO_EMAIL,
        },
        workflow.targets,
        lineage.directory / "runs",
        settings=replay_settings(),
        signed_in=False,
        model=model,
        abstain_steps=frozenset(s for s, key in workflow.targets.items() if key in abstain_keys),
        asked=asked,
        patcher=lineage.patcher(),
        source=decision.source,
    )
    return Ran(decision, replay, tuple(asked))


@dataclass(frozen=True, slots=True)
class Healed:
    """Level 3 seed 3 run twice from an empty store, with the first run's report."""

    lineage: Lineage
    first: Ran
    calls_after_first: int
    rerun: Ran
    calls_after_rerun: int
    report: Path


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def level_3_seed_3(
    browser: Browser, portal_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Healed:
    lineage = Lineage(tmp_path_factory.mktemp("level-3-seed-3"), PromotionPolicy.IMMEDIATE)
    first = await run_file(browser, portal_url, lineage, level=3, seed=HEALING_SEED, model="oracle")
    calls_after_first = lineage.ledger_calls()
    rerun = await run_file(browser, portal_url, lineage, level=3, seed=HEALING_SEED, model="oracle")
    artifacts = LocalArtifactStore(lineage.directory / "runs")
    writer = ReportWriter(
        artifacts=artifacts,
        read=artifacts.read_now,
        scrubber=SecretScrubber(),
        budget_bytes=replay_settings().report_screenshots_max_bytes,
    )
    name = await writer.write(first.run, first.decision.version)
    report = artifacts.run_directory(first.run.run_id) / name
    return Healed(lineage, first, calls_after_first, rerun, lineage.ledger_calls(), report)


async def test_a_level_3_heal_by_the_model_is_verified_and_saved_as_v2(
    level_3_seed_3: Healed,
) -> None:
    first = level_3_seed_3.first
    v1, v2 = await level_3_seed_3.lineage.versions()

    assert first.run.status is RunStatus.SUCCEEDED, first.run.error
    assert first.replay.wrong() == ()
    assert (first.decision.version.version, first.decision.publish_first) == (1, True)
    assert first.healed() == [("open_reports", 3)]
    assert first.run.model_usage.calls == level_3_seed_3.calls_after_first == 1
    assert first.patches() == [("open_reports", PatchResult.PUBLISHED, 2)]
    assert [step.id for step in v2.steps] == [step.id for step in v1.steps]
    assert [new.id for old, new in zip(v1.steps, v2.steps, strict=True) if old != new] == [
        "open_reports"
    ]
    change = v2.change
    assert isinstance(change, HealChange)
    assert (change.step_id, change.rung, change.evidence.run_id) == (
        "open_reports",
        3,
        first.run.run_id,
    )
    assert change.strength is not VerificationStrength.NONE
    assert change.model is not None
    assert change.model.calls == 1
    healed_step = next(step for step in first.run.steps if step.step_id == "open_reports")
    assert healed_step.found is not None
    assert healed_step.found.fingerprint == step_target(v2.steps[4]) == change.new_target


async def test_rerunning_the_same_file_on_the_same_seed_needs_no_heal_and_no_model_call(
    level_3_seed_3: Healed,
) -> None:
    rerun = level_3_seed_3.rerun
    checked = [check.correct for check in rerun.replay.checks if check.target_key is not None]

    assert rerun.run.status is RunStatus.SUCCEEDED, rerun.run.error
    assert rerun.replay.wrong() == ()
    assert checked
    assert all(checked)
    source = rerun.decision.source
    assert (rerun.decision.version.version, source.stored_version, source.ran_stored) == (
        2,
        1,
        True,
    )
    assert rerun.healed() == []
    assert rerun.replay.tracker.scans == {}
    assert rerun.first_tries() == []
    assert rerun.run.model_usage.calls == 0
    assert rerun.asked == ()
    assert level_3_seed_3.calls_after_rerun == level_3_seed_3.calls_after_first
    assert rerun.run.patches == ()
    opened = next(step for step in rerun.run.steps if step.step_id == "open_reports")
    assert opened.target is not None
    assert opened.target.resolved_rank is not None
    assert [version.version for version in await level_3_seed_3.lineage.versions()] == [1, 2]


async def test_the_first_run_s_report_shows_the_model_s_heal_and_loads_nothing(
    browser: Browser, level_3_seed_3: Healed
) -> None:
    html = level_3_seed_3.report.read_text(encoding="utf-8")

    loaded = await load_report(browser, html)

    assert (loaded.requests, loaded.policies, loaded.marks_inside) == ((), 1, (True,))
    assert loaded.images >= 2
    assert "Healed at rung 3, chosen by an AI model" in html
    assert "What the model was shown" in html
    assert "saved as download_report v2 (rung 3" in html
    assert DEMO_PASSWORD not in html


async def test_the_rule_seed_is_the_smallest_other_level_3_seed_that_mutates_the_target(
    browser: Browser, portal_url: str
) -> None:
    key = load_workflows()[WORKFLOW].targets["open_reports"]
    mutating = []
    for seed in range(1, RULE_SEED + 1):
        applied = await ground_truth(browser, portal_url, seed, 3)
        if any(item.target_key == key for items in applied.values() for item in items):
            mutating.append(seed)

    assert HEALING_SEED > RULE_SEED
    assert mutating == [RULE_SEED]


@pytest.mark.parametrize(("level", "seed"), [(0, 0), (3, RULE_SEED)])
async def test_where_v2_s_target_no_longer_matches_the_run_heals_again_and_acts_on_no_wrong_element(
    browser: Browser,
    portal_url: str,
    level_3_seed_3: Healed,
    tmp_path: Path,
    level: int,
    seed: int,
) -> None:
    lineage = level_3_seed_3.lineage.copied_to(tmp_path)

    ran = await run_file(browser, portal_url, lineage, level=level, seed=seed, model="oracle")

    assert ran.replay.wrong() == ()
    assert ran.decision.version.version == 2
    assert ran.run.status is RunStatus.SUCCEEDED, ran.run.error
    assert ran.healed() == [("open_reports", 2)]
    assert ran.run.model_usage.calls == 0
    assert ran.patches() == [("open_reports", PatchResult.PUBLISHED, 3)]


async def test_after_two_successes_a_pending_heal_is_tried_first_then_saved_then_never_needed(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    lineage = Lineage(tmp_path, PromotionPolicy.AFTER_N_SUCCESSES)

    first = await run_file(browser, portal_url, lineage, level=3, seed=0, model="none")
    second = await run_file(browser, portal_url, lineage, level=3, seed=0, model="none")
    third = await run_file(browser, portal_url, lineage, level=3, seed=0, model="none")

    for ran in (first, second, third):
        assert ran.run.status is RunStatus.SUCCEEDED, ran.run.error
        assert ran.replay.wrong() == ()
    assert first.healed() == [("download_csv", 2)]
    assert [
        (item.step_id, item.result, item.successes, item.required) for item in first.run.patches
    ] == [("download_csv", PatchResult.PENDING, 1, 2)]
    assert (second.decision.version.version, second.healed(), second.first_tries()) == (
        1,
        [],
        ["download_csv"],
    )
    assert second.replay.tracker.scans == {}
    assert [
        (item.step_id, item.result, item.version, item.successes) for item in second.run.patches
    ] == [("download_csv", PatchResult.PUBLISHED, 2, 2)]
    assert (third.decision.version.version, third.healed(), third.first_tries()) == (2, [], [])
    assert (third.replay.tracker.scans, third.run.patches) == ({}, ())
    assert [version.version for version in await lineage.versions()] == [1, 2]
    assert await lineage.pending.read(WORKFLOW) == ()
