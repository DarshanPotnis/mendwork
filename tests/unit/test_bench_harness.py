"""The benchmark harness's pure parts: script locators, target maps, systems, and provenance."""

import json
import shutil
from pathlib import Path
from typing import Final

import pytest

from benchmarks.baselines.script_runner import new_run_id, script_selector
from benchmarks.chaos.bench import (
    BenchmarkSetupError,
    load_bench_workflows,
    settings_overrides,
    source_digest,
)
from benchmarks.chaos.systems import (
    CSS_SELECTOR,
    DEFAULT_SYSTEMS,
    LADDER_FREE,
    LADDER_GROUND_TRUTH,
    LADDER_MODEL,
    ROLE_NAME,
    SYSTEM_IDS,
    describe,
    is_script,
    model_mode,
    script_kind,
)
from benchmarks.chaos.workflow_targets import WORKFLOW_TARGETS_DIR, load_workflow_targets
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.benchmark.results import SystemKind
from mendwork.engine.domain.enums import SelectorStrategy
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.run_identifiers import parse_run_id
from mendwork.engine.domain.selectors import ByCss, ByLabel, ByRole
from mendwork.engine.domain.steps import ClickStep, FillStep
from mendwork.settings import Settings

REPO: Final = Path(__file__).resolve().parents[2]
EXAMPLES: Final = REPO / "workflows" / "examples"


def target(workflow_id: str, step_id: str) -> Fingerprint:
    path = EXAMPLES / f"{workflow_id}.yaml"
    workflow = WorkflowYamlCodec(max_bytes=1 << 20).decode(path.read_bytes(), source=str(path))
    step = next(step for step in workflow.steps if step.id == step_id)
    assert isinstance(step, ClickStep | FillStep)
    return step.target


def test_the_css_script_uses_the_recorded_css_selector() -> None:
    selector = script_selector(target("download_report", "sign_in"), "css_selector")

    assert isinstance(selector, ByCss)
    assert selector.value == "#sign-in"


def test_the_role_script_uses_role_and_name_and_a_label_for_fields_without_a_role() -> None:
    button = script_selector(target("download_report", "sign_in"), "role_name")
    password = script_selector(target("download_report", "fill_password"), "role_name")
    scoped = script_selector(target("view_order_detail", "open_orders"), "role_name")

    assert isinstance(button, ByRole)
    assert (button.name, button.exact) == ("Sign in", True)
    assert isinstance(password, ByLabel)
    assert password.value == "Password"
    assert isinstance(scoped, ByRole)
    assert scoped.within is not None


def test_a_target_without_a_selector_of_the_scripts_kind_has_no_script_locator() -> None:
    recorded = target("download_report", "fill_date_from")
    only_test_id = recorded.model_copy(
        update={
            "selectors": tuple(
                item for item in recorded.selectors if item.strategy is SelectorStrategy.TEST_ID
            )
        }
    )

    assert script_selector(only_test_id, "css_selector") is None
    assert script_selector(only_test_id, "role_name") is None


def test_a_target_map_fills_its_input_templates_for_a_run() -> None:
    mapping = load_workflow_targets("download_report")

    assert mapping.run_inputs(portal="http://127.0.0.1:9/", seed=1000, level=5) == {
        "portal_url": "http://127.0.0.1:9/index.html?seed=1000&level=5",
        "account_email": "buyer@harborline.test",
    }
    assert set(mapping.secrets) == {"portal_password"}


def test_a_target_map_with_an_unknown_placeholder_or_another_workflows_id_is_refused(
    tmp_path: Path,
) -> None:
    document = json.loads((WORKFLOW_TARGETS_DIR / "download_report.json").read_text("utf-8"))
    (tmp_path / "download_report.json").write_text(
        json.dumps({**document, "inputs": {"portal_url": "{host}/index.html"}}), encoding="utf-8"
    )
    (tmp_path / "view_order_detail.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=r"unknown placeholders \['host'\]"):
        load_workflow_targets("download_report", tmp_path)
    with pytest.raises(ValueError, match="maps download_report, not view_order_detail"):
        load_workflow_targets("view_order_detail", tmp_path)


def test_the_examples_load_with_their_maps_in_a_stable_order() -> None:
    loaded = load_bench_workflows(EXAMPLES, max_bytes=1 << 20)

    assert [workflow.version.workflow_id for workflow in loaded] == [
        "download_report",
        "view_order_detail",
    ]


def test_a_workflow_directory_the_benchmark_cannot_use_is_refused_with_the_reason(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    unmapped = tmp_path / "unmapped"
    unmapped.mkdir()
    shutil.copy(EXAMPLES / "download_report.yaml", unmapped)
    maps = tmp_path / "maps"
    maps.mkdir()
    document = json.loads((WORKFLOW_TARGETS_DIR / "download_report.json").read_text("utf-8"))
    document["targets"]["no_such_step"] = "login.email"
    (maps / "download_report.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(BenchmarkSetupError, match="no workflow files"):
        load_bench_workflows(empty, max_bytes=1 << 20)
    with pytest.raises(
        BenchmarkSetupError, match=r"download_report\.yaml: no ground-truth target map"
    ):
        load_bench_workflows(unmapped, targets_dir=tmp_path / "none", max_bytes=1 << 20)
    with pytest.raises(BenchmarkSetupError, match=r"lacks: \['no_such_step'\]"):
        load_bench_workflows(unmapped, targets_dir=maps, max_bytes=1 << 20)


def test_each_system_is_described_and_only_mendwork_is_gated() -> None:
    described = {system: describe(system, model="qwen3:4b") for system in SYSTEM_IDS}

    assert {system for system, item in described.items() if item.gated} == {
        LADDER_FREE,
        LADDER_GROUND_TRUTH,
        LADDER_MODEL,
    }
    assert {system for system, item in described.items() if item.kind is SystemKind.SCRIPT} == {
        CSS_SELECTOR,
        ROLE_NAME,
    }
    assert "upper bound" in described[LADDER_GROUND_TRUTH].label
    assert described[LADDER_MODEL].label == "Mendwork + qwen3:4b (local)"
    assert describe(LADDER_MODEL, model="qwen3:4b-instruct-2507-q4_K_M").short_label == (
        "Mendwork + qwen3:4b"
    )
    assert all(len(item.short_label) <= 28 for item in described.values())
    assert LADDER_MODEL not in DEFAULT_SYSTEMS


def test_systems_refuse_questions_that_do_not_apply_to_them() -> None:
    assert is_script(CSS_SELECTOR)
    assert not is_script(LADDER_FREE)
    assert script_kind(ROLE_NAME) == "role_name"
    assert [model_mode(system) for system in (LADDER_FREE, LADDER_GROUND_TRUTH, LADDER_MODEL)] == [
        "none",
        "oracle",
        "configured",
    ]
    with pytest.raises(ValueError, match="not a script system"):
        script_kind(LADDER_FREE)
    with pytest.raises(ValueError, match="not a ladder system"):
        model_mode(CSS_SELECTOR)
    with pytest.raises(ValueError, match="needs the configured model"):
        describe(LADDER_MODEL)
    with pytest.raises(ValueError, match="unknown system"):
        describe("selenium")


def test_provenance_records_every_changed_setting_and_nothing_else() -> None:
    changed = Settings.model_construct().model_copy(
        update={"step_timeout_ms": 1_500, "trace_on_failure": False}
    )

    assert settings_overrides(changed) == {"step_timeout_ms": 1500, "trace_on_failure": False}


def test_a_source_digest_follows_content_and_ignores_other_files(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "__pycache__").mkdir(parents=True)
    (tree / "module.py").write_text("x = 1\n", encoding="utf-8")
    before = source_digest(tree, (".py",))

    (tree / "notes.txt").write_text("ignored", encoding="utf-8")
    (tree / "__pycache__" / "cached.py").write_text("ignored", encoding="utf-8")
    unchanged = source_digest(tree, (".py",))
    (tree / "module.py").write_text("x = 2\n", encoding="utf-8")

    assert unchanged == before
    assert source_digest(tree, (".py",)) != before
    assert source_digest(tree / "module.py", (".py",)).startswith("sha256:")


def test_a_source_digest_leaves_out_what_it_is_told_to(tmp_path: Path) -> None:
    tree = tmp_path / "benchmarks"
    (tree / "results").mkdir(parents=True)
    (tree / "bench.py").write_text("x = 1\n", encoding="utf-8")
    before = source_digest(tree, (".json", ".py"), exclude=(tree / "results",))

    (tree / "results" / "chaos-results.json").write_text("{}", encoding="utf-8")

    assert source_digest(tree, (".json", ".py"), exclude=(tree / "results",)) == before
    assert source_digest(tree, (".json", ".py")) != before


def test_a_script_session_is_named_like_a_mendwork_run() -> None:
    assert parse_run_id(new_run_id())
