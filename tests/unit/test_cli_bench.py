"""``mendwork bench``: its arguments, the scorecard command, and running without the harness."""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from mendwork.adapters.benchmark_fs.files import encode_results
from mendwork.apps.cli.main import app
from tests.unit.reporting.test_scorecard_view import grid_document, unjudged_document


def chaos_arguments(workflows: Path, output: Path, *extra: str) -> list[str]:
    return [
        "bench",
        "chaos",
        "--workflows",
        str(workflows),
        "--level",
        "5",
        "--seeds",
        "1",
        "--output",
        str(output),
        *extra,
    ]


def test_bench_chaos_says_where_it_runs_when_the_harness_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "benchmarks", None)

    result = CliRunner().invoke(app, chaos_arguments(tmp_path, tmp_path / "out"))

    assert result.exit_code == 2
    assert "runs from a source checkout of Mendwork" in result.stderr


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--system", "css_selector", "--system", "css_selector"), "systems must be distinct"),
        (("--system", "selenium"), "systems must be distinct and among css_selector"),
        (("--level", "6"), "levels are 0 to 5"),
    ],
)
def test_bench_chaos_refuses_systems_and_levels_it_does_not_know(
    tmp_path: Path, extra: tuple[str, ...], message: str
) -> None:
    result = CliRunner().invoke(app, chaos_arguments(tmp_path, tmp_path / "out", *extra))

    assert result.exit_code == 2
    assert message in result.stderr


def test_bench_chaos_refuses_a_directory_without_workflows(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, chaos_arguments(tmp_path, tmp_path / "out"))

    assert result.exit_code == 2
    assert f"no workflow files (*.yaml) in {tmp_path}" in result.stderr
    assert not (tmp_path / "out").exists()


def test_bench_chaos_refuses_an_included_file_that_is_not_a_results_document(
    tmp_path: Path,
) -> None:
    included = tmp_path / "real-app-results.json"
    included.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        app, chaos_arguments(tmp_path, tmp_path / "out", "--include", str(included))
    )

    assert result.exit_code == 2
    assert "validation error" in result.stderr


def test_bench_scorecard_writes_one_page_from_results_files(
    plain_stdout: Callable[[Result], str], tmp_path: Path
) -> None:
    results = tmp_path / "chaos-results.json"
    results.write_bytes(encode_results(grid_document()))
    output = tmp_path / "scorecard.html"

    result = CliRunner().invoke(
        app, ["bench", "scorecard", "--results", str(results), "--output", str(output)]
    )

    assert result.exit_code == 0
    assert plain_stdout(result) == f"wrote {output}\n"
    assert "<title>Mendwork benchmark scorecard</title>" in output.read_text(encoding="utf-8")


def test_bench_scorecard_refuses_a_file_that_is_not_a_results_document(tmp_path: Path) -> None:
    results = tmp_path / "chaos-results.json"
    results.write_text('{"schema_version": 1}', encoding="utf-8")

    result = CliRunner().invoke(
        app, ["bench", "scorecard", "--results", str(results), "--output", str(tmp_path / "s.html")]
    )

    assert result.exit_code == 2
    assert "not a valid benchmark results document" in result.stderr


def test_bench_scorecard_reports_an_output_it_cannot_write(tmp_path: Path) -> None:
    results = tmp_path / "chaos-results.json"
    results.write_bytes(encode_results(grid_document()))
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["bench", "scorecard", "--results", str(results), "--output", str(blocker / "s.html")],
    )

    assert result.exit_code == 3
    assert "could not write the scorecard" in result.stderr


def test_an_included_results_file_follows_this_runs_own_section(tmp_path: Path) -> None:
    """The run being measured leads the scorecard; a pair shown beside it comes after."""
    chaos = tmp_path / "chaos-results.json"
    chaos.write_bytes(encode_results(grid_document()))
    pair = tmp_path / "real-app-results.json"
    pair.write_bytes(encode_results(unjudged_document()))
    output = tmp_path / "scorecard.html"

    result = CliRunner().invoke(
        app,
        [
            "bench",
            "scorecard",
            "--results",
            str(chaos),
            "--results",
            str(pair),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    page = output.read_text(encoding="utf-8")
    assert page.index("Chaos grid") < page.index("A pair.")


def test_a_run_records_the_operator_notes_it_was_given(tmp_path: Path) -> None:
    """Latency depends on what else the machine was doing, which only a person can say."""
    from benchmarks.chaos.bench import ChaosBenchPlan
    from benchmarks.real_apps.gitea.pair import plan as gitea_plan

    chaos = ChaosBenchPlan(
        workflows=(),
        levels=(2,),
        seeds=(1000,),
        systems=("ladder_free",),
        concurrency=1,
        single_mutations=False,
        notes=("Colima stopped.",),
    )
    pair = gitea_plan(("ladder_free",), max_bytes=1 << 20, notes=("Colima running.",))

    assert chaos.notes == ("Colima stopped.",)
    assert pair.notes == ("Colima running.",)
