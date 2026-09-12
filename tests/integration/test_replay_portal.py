"""The example workflows replayed in real Chromium against the chaos portal, in-process.

Mutation scenarios run the complete download_report workflow from the unmutated sign-in
page, with one extra navigate step after ``open_reports`` that reloads Reports with
``?seed=&only=``. The portal applies ``only=`` on every page with an eligible target, and
the login page has one for every mutation used here, so chaos can only be scoped to
Reports by a URL that carries it; the committed workflow reaches Reports through a link
without parameters. Seeds come from heal_pairs.json and the portal's own selection, and
each test asserts that its seed chose the intended target.
"""

import asyncio
import json
import zipfile
from pathlib import Path

import pytest
from playwright.async_api import Browser, Page

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.documents import parse_workflow_document, workflow_document
from mendwork.engine.domain.events import TargetResolvedEvent
from mendwork.engine.domain.runs import RunStatus, StepStatus
from mendwork.engine.domain.workflow import WorkflowVersion
from tests.integration.portal import DEMO_EMAIL, DEMO_PASSWORD, ChaosSnapshot, PortalDriver
from tests.integration.replay_harness import ReplayOutcome, replay, replay_settings
from tests.workflows import Document, example_path, load_example

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

SECRETS = {"portal_password": DEMO_PASSWORD}
CSV_HEADER = "shipment_id,ship_date,order_id,supplier,sku,quantity,amount_usd"
FILTER_TEXT = "14 shipments between Feb 10, 2026 and Apr 20, 2026"


def example_inputs(portal_url: str) -> dict[str, str]:
    return {"portal_url": f"{portal_url}index.html", "account_email": DEMO_EMAIL}


def with_chaos_on_reports(workflow: WorkflowVersion) -> WorkflowVersion:
    """download_report plus one navigate step that reloads Reports with chaos applied."""
    document: Document = workflow_document(workflow)
    steps = document["steps"]
    position = next(i for i, step in enumerate(steps) if step["id"] == "open_reports") + 1
    steps.insert(
        position,
        {
            "id": "load_reports_with_chaos",
            "intent": "Reload the reports page with chaos applied",
            "action": "navigate",
            "risk": "safe",
            "value": {"kind": "input", "name": "reports_url"},
            "checkpoints": [
                {
                    "kind": "element_visible",
                    "selector": {
                        "strategy": "role_name",
                        "role": "heading",
                        "name": "Shipment reports",
                    },
                }
            ],
        },
    )
    document["inputs"].append({"name": "reports_url", "kind": "url"})
    return parse_workflow_document(document)


async def replay_with_chaos(
    browser: Browser, portal_url: str, tmp_path: Path, seed: int, mutation: str, **timing: int
) -> tuple[ReplayOutcome, ChaosSnapshot]:
    snapshots: list[ChaosSnapshot] = []

    async def inspect(page: Page) -> None:
        # Test-side ground truth, read after the run and before its context closes.
        snapshots.append(await PortalDriver(page, portal_url).state())

    outcome = await replay(
        browser,
        with_chaos_on_reports(load_example("download_report")),
        {
            **example_inputs(portal_url),
            "reports_url": f"{portal_url}reports.html?seed={seed}&only={mutation}",
        },
        tmp_path,
        secrets=SECRETS,
        settings=replay_settings(**timing),
        inspect=inspect,
    )
    [chaos] = snapshots
    return outcome, chaos


def applied(chaos: ChaosSnapshot) -> list[tuple[str, str | None]]:
    return [(mutation.id, mutation.target_key) for mutation in chaos.applied]


def resolved_rank(outcome: ReplayOutcome, step_id: str) -> int | None:
    [event] = outcome.events_for(step_id, "target_resolved")
    assert isinstance(event, TargetResolvedEvent)
    return event.evidence.resolved_rank


async def read(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def test_download_report_succeeds_end_to_end_at_level_zero(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    workflow = load_example("download_report")

    outcome = await replay(browser, workflow, example_inputs(portal_url), tmp_path, secrets=SECRETS)

    assert outcome.run.status is RunStatus.SUCCEEDED, outcome.run.error
    download = outcome.step("download_csv").artifacts.download
    assert download == "downloads/shipments_2026-02-10_to_2026-04-20.csv"
    lines = (await read(outcome.run_directory / download)).splitlines()
    assert lines[0] == CSV_HEADER
    assert len(lines) - 1 == 14
    screenshots = sorted(path.name for path in (outcome.run_directory / "steps").iterdir())
    assert screenshots == [
        f"{index + 1:03d}_{step.id}.png" for index, step in enumerate(workflow.steps)
    ]
    assert json.loads(await read(outcome.run_directory / "run.json"))["status"] == "succeeded"
    targeted = [step.id for step in workflow.steps if step.action != "navigate"]
    assert [resolved_rank(outcome, step_id) for step_id in targeted] == [0] * len(targeted)


async def test_view_order_detail_succeeds_end_to_end_at_level_zero(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    outcome = await replay(
        browser,
        load_example("view_order_detail"),
        example_inputs(portal_url),
        tmp_path,
        secrets=SECRETS,
    )

    assert outcome.run.status is RunStatus.SUCCEEDED, outcome.run.error
    assert outcome.step("view_po_1042").checkpoints[0].passed


async def test_regenerated_ids_resolve_through_a_lower_ranked_selector(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    outcome, chaos = await replay_with_chaos(
        browser, portal_url, tmp_path, 12, "change_ids_classes"
    )

    assert applied(chaos) == [("change_ids_classes", "reports.download_csv")]
    assert outcome.run.status is RunStatus.SUCCEEDED, outcome.run.error
    assert resolved_rank(outcome, "download_csv") == 1
    [event] = outcome.events_for("download_csv", "target_resolved")
    assert isinstance(event, TargetResolvedEvent)
    assert [report.outcome for report in event.evidence.selectors] == ["none", "hit", "hit", "none"]


async def test_a_synonym_rename_stops_as_a_drifted_match_with_both_names(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    outcome, chaos = await replay_with_chaos(browser, portal_url, tmp_path, 12, "synonym_rename")

    assert applied(chaos) == [("synonym_rename", "reports.download_csv")]
    step = outcome.step("download_csv")
    assert step.error is not None
    assert step.error.type == "TargetDrifted"
    recorded = step.error.context["recorded"]
    found = step.error.context["found"]
    assert isinstance(recorded, dict)
    assert isinstance(found, dict)
    assert recorded["name"] == "Download CSV"
    assert found["name"] != "Download CSV"
    assert f'"{found["name"]}"' in chaos.applied[0].description
    assert not step.action_performed
    assert chaos.wrong_actions == ()


async def test_a_dangerous_rename_stops_before_any_click(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    outcome, chaos = await replay_with_chaos(browser, portal_url, tmp_path, 1, "dangerous_rename")

    assert applied(chaos) == [("dangerous_rename", "reports.download_csv")]
    step = outcome.step("download_csv")
    assert step.error is not None
    assert step.error.type == "TargetDrifted"
    found = step.error.context["found"]
    assert isinstance(found, dict)
    assert found["name"] == "Delete data"
    assert step.error.context["differences"] == ["accessible_name"]
    assert not step.action_performed
    assert step.artifacts.download is None
    assert chaos.wrong_actions == ()


async def test_two_plausible_copies_are_ambiguous_and_neither_is_clicked(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    outcome, chaos = await replay_with_chaos(
        browser, portal_url, tmp_path, 1, "duplicate_plausible"
    )

    assert applied(chaos) == [("duplicate_plausible", "reports.download_csv")]
    step = outcome.step("download_csv")
    assert step.error is not None
    assert (step.error.type, step.error.context["reason"]) == ("AmbiguousTarget", "several_matches")
    assert chaos.wrong_actions == ()


async def test_a_removed_control_is_not_found_and_nothing_is_clicked(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    outcome, chaos = await replay_with_chaos(
        browser, portal_url, tmp_path, 1, "remove_target", step_timeout_ms=1_500
    )

    assert applied(chaos) == [("remove_target", "reports.download_csv")]
    step = outcome.step("download_csv")
    assert step.error is not None
    assert step.error.type == "TargetNotFound"
    assert chaos.wrong_actions == ()


@pytest.mark.parametrize(
    ("seed", "mutation", "target"),
    [(17, "reorder_siblings", "reports.date_from"), (1, "icon_only_aria", "reports.download_csv")],
)
async def test_harmless_layout_changes_still_succeed_with_the_right_dates(
    browser: Browser, portal_url: str, tmp_path: Path, seed: int, mutation: str, target: str
) -> None:
    outcome, chaos = await replay_with_chaos(browser, portal_url, tmp_path, seed, mutation)

    assert applied(chaos) == [(mutation, target)]
    assert outcome.run.status is RunStatus.SUCCEEDED, outcome.run.error
    filter_check = outcome.step("apply_filter").checkpoints
    assert [(result.kind, result.passed) for result in filter_check] == [
        ("text_present", True),
        ("no_error_banner", True),
    ]


async def test_a_wrong_date_fails_the_filter_checkpoint_with_full_evidence(
    browser: Browser, portal_url: str, tmp_path: Path
) -> None:
    source = await read(example_path("download_report"))
    assert source.count('value: "2026-04-20"') == 1
    copy = tmp_path / "download_report_wrong_date.yaml"
    await asyncio.to_thread(
        copy.write_text,
        source.replace('value: "2026-04-20"', 'value: "2026-04-21"'),
        encoding="utf-8",
    )
    workflow = WorkflowYamlCodec(max_bytes=1 << 20).decode(
        await asyncio.to_thread(copy.read_bytes), source=str(copy)
    )

    outcome = await replay(
        browser,
        workflow,
        example_inputs(portal_url),
        tmp_path,
        secrets=SECRETS,
        settings=replay_settings(checkpoint_timeout_ms=1_500),
    )

    step = outcome.step("apply_filter")
    assert step.error is not None
    assert (step.error.type, step.error.context["kind"]) == ("CheckpointFailed", "text_present")
    assert [s.status for s in outcome.run.steps[-1:]] == [StepStatus.NOT_RUN]
    artifacts = step.artifacts
    assert (
        artifacts.screenshot,
        artifacts.dom_snapshot,
        artifacts.trace,
        artifacts.trace_withheld,
    ) == (
        "steps/008_apply_filter.png",
        "failure/008_apply_filter.dom.html",
        "failure/trace.zip",
        None,
    )
    assert FILTER_TEXT not in await read(
        outcome.run_directory / "failure/008_apply_filter.dom.html"
    )
    with zipfile.ZipFile(outcome.run_directory / "failure/trace.zip") as archive:
        assert "trace.trace" in archive.namelist()
