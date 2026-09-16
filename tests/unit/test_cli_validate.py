"""``mendwork validate`` and ``mendwork schema`` report through stdout and exit codes."""

from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from mendwork.apps.cli.main import app
from mendwork.engine.benchmark.results import results_json_schema_text
from mendwork.engine.domain.json_schema import workflow_json_schema_text
from tests.workflows import EXAMPLE_IDS, example_path


@pytest.mark.parametrize(
    ("workflow_id", "summary"),
    [
        ("download_report", "download_report v1 — 9 steps, 2 inputs, 1 secret, 11 checkpoints"),
        ("view_order_detail", "view_order_detail v1 — 6 steps, 2 inputs, 1 secret, 7 checkpoints"),
    ],
)
def test_a_valid_workflow_prints_ok_with_a_summary(
    plain_stdout: Callable[[Result], str], workflow_id: str, summary: str
) -> None:
    path = example_path(workflow_id)

    result = CliRunner().invoke(app, ["validate", str(path)])

    assert result.exit_code == 0
    assert plain_stdout(result) == f"OK {path}: {summary}\n"


def test_every_example_is_covered() -> None:
    assert sorted(
        path.stem for path in example_path(EXAMPLE_IDS[0]).parent.glob("*.yaml")
    ) == sorted(EXAMPLE_IDS)


def test_a_broken_workflow_prints_every_problem_with_its_line_and_exits_1(
    plain_stdout: Callable[[Result], str], tmp_path: Path
) -> None:
    text = example_path("download_report").read_text(encoding="utf-8")
    broken = (
        text.replace(
            "value: {kind: secret, name: portal_password}", "value: {kind: literal, value: hunter2}"
        )
        .replace(
            "risk: caution                        # changes session",
            "risk: careful  # changes session",
        )
        .replace(
            "        - {strategy: test_id, value: download-csv}",
            "        - {stratgy: test_id, value: download-csv}",
        )
    )
    path = tmp_path / "broken.yaml"
    path.write_text(broken, encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(path)])

    assert result.exit_code == 1
    lines = plain_stdout(result).splitlines()
    assert lines[0] == f"{path}: 3 problems"
    assert lines[1].startswith(
        f"{path}:69:5: steps[2].value (step fill_password): the target looks like a password"
    )
    assert (
        lines[2]
        == f"{path}:76:5: steps[3].risk (step sign_in): must be one of: 'safe', 'caution' or "
        "'irreversible'"
    )
    assert (
        lines[3]
        == f"{path}:196:11: steps[8].target.selectors[0] (step download_csv): needs a 'strategy' "
        "field; found 'stratgy', did you mean 'strategy'?"
    )
    assert "hunter2" not in result.output


def test_a_file_level_problem_prints_without_a_path(
    plain_stdout: Callable[[Result], str], tmp_path: Path
) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(path)])

    assert result.exit_code == 1
    assert plain_stdout(result) == f"{path}: 1 problem\n{path}:1:1: the file is empty\n"


def test_a_missing_file_is_a_usage_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["validate", str(tmp_path / "missing.yaml")])

    assert result.exit_code == 2


def test_the_size_limit_comes_from_settings(
    plain_stdout: Callable[[Result], str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MENDWORK_WORKFLOW_MAX_BYTES", "4096")
    path = tmp_path / "big.yaml"
    path.write_text("# padding\n" * 1000, encoding="utf-8")

    result = CliRunner().invoke(app, ["validate", str(path)])

    assert result.exit_code == 1
    assert "the file is larger than the 4096-byte limit" in plain_stdout(result)


def test_schema_writes_the_generated_schema(
    plain_stdout: Callable[[Result], str], tmp_path: Path
) -> None:
    output = tmp_path / "workflow.schema.json"

    result = CliRunner().invoke(app, ["schema", "--output", str(output)])

    assert result.exit_code == 0
    assert plain_stdout(result) == f"wrote {output}\n"
    assert output.read_text(encoding="utf-8") == workflow_json_schema_text()


def test_schema_also_writes_the_benchmark_results_schema_when_asked(
    plain_stdout: Callable[[Result], str], tmp_path: Path
) -> None:
    output = tmp_path / "workflow.schema.json"
    results = tmp_path / "bench-results.schema.json"

    result = CliRunner().invoke(
        app, ["schema", "--output", str(output), "--results-output", str(results)]
    )

    assert result.exit_code == 0
    assert plain_stdout(result) == f"wrote {output}\nwrote {results}\n"
    assert results.read_text(encoding="utf-8") == results_json_schema_text()
