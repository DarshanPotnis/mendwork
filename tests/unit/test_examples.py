"""The example workflows are valid, fully mapped to chaos targets, and tolerant of query strings."""

from pathlib import Path

import pytest

from benchmarks.chaos.workflow_targets import WORKFLOW_TARGETS_DIR, load_workflow_targets
from mendwork.engine.domain.checkpoints import UrlMatches
from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.steps import step_target
from tests.integration.portal import ALL_TARGET_KEYS
from tests.workflows import EXAMPLE_IDS, load_example


@pytest.mark.parametrize("workflow_id", EXAMPLE_IDS)
def test_every_targeted_step_maps_to_a_declared_chaos_target(workflow_id: str) -> None:
    workflow = load_example(workflow_id)
    mapping = load_workflow_targets(workflow_id)

    targeted = [step.id for step in workflow.steps if step_target(step) is not None]
    assert list(mapping.targets) == targeted
    assert set(mapping.targets.values()) <= ALL_TARGET_KEYS


def test_every_mapping_file_belongs_to_an_example() -> None:
    assert sorted(path.stem for path in WORKFLOW_TARGETS_DIR.glob("*.json")) == sorted(EXAMPLE_IDS)


def test_a_mapping_for_the_wrong_workflow_is_refused(tmp_path: Path) -> None:
    (tmp_path / "download_report.json").write_text(
        '{"workflow_id": "view_order_detail", "targets": {}}', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="maps view_order_detail, not download_report"):
        load_workflow_targets("download_report", tmp_path)


@pytest.mark.parametrize("workflow_id", EXAMPLE_IDS)
def test_sign_in_is_caution_because_it_changes_session_state_reversibly(workflow_id: str) -> None:
    workflow = load_example(workflow_id)

    [sign_in] = [step for step in workflow.steps if step.id == "sign_in"]
    assert sign_in.risk is RiskLevel.CAUTION


URL_CHECKPOINT_PAGES = {
    ("download_report", "sign_in"): "dashboard.html",
    ("download_report", "open_reports"): "reports.html",
    ("view_order_detail", "sign_in"): "dashboard.html",
    ("view_order_detail", "open_orders"): "orders.html",
}
BASES = ("http://127.0.0.1:8765/", "https://example.github.io/mendwork/chaos-portal/")
SUFFIXES = ("", "?seed=12&level=3", "#main", "?seed=12&level=3#main")


def url_checkpoints() -> list[tuple[str, str, UrlMatches]]:
    found: list[tuple[str, str, UrlMatches]] = []
    for workflow_id in EXAMPLE_IDS:
        for step in load_example(workflow_id).steps:
            found.extend(
                (workflow_id, step.id, checkpoint)
                for checkpoint in step.checkpoints
                if isinstance(checkpoint, UrlMatches)
            )
    return found


def test_the_table_covers_every_url_checkpoint_in_the_examples() -> None:
    assert sorted(
        (workflow_id, step_id) for workflow_id, step_id, _ in url_checkpoints()
    ) == sorted(URL_CHECKPOINT_PAGES)


@pytest.mark.parametrize(("workflow_id", "step_id", "checkpoint"), url_checkpoints())
def test_url_patterns_match_with_and_without_query_and_fragment(
    workflow_id: str, step_id: str, checkpoint: UrlMatches
) -> None:
    page = URL_CHECKPOINT_PAGES[(workflow_id, step_id)]

    for base in BASES:
        for suffix in SUFFIXES:
            assert checkpoint.matches_url(f"{base}{page}{suffix}"), f"{base}{page}{suffix}"


@pytest.mark.parametrize(("workflow_id", "step_id", "checkpoint"), url_checkpoints())
def test_url_patterns_reject_other_pages(
    workflow_id: str, step_id: str, checkpoint: UrlMatches
) -> None:
    page = URL_CHECKPOINT_PAGES[(workflow_id, step_id)]
    others = {"index.html", "dashboard.html", "reports.html", "orders.html"} - {page}
    stem = page.removesuffix(".html")

    for url in [f"{BASES[0]}{other}?seed=12&level=3" for other in others] + [
        f"{BASES[0]}{stem}-archive.html",
        f"{BASES[0]}{page}.bak",
        f"{BASES[0]}index.html?next=/{page}",
    ]:
        assert not checkpoint.matches_url(url), url
