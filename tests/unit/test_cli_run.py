"""``mendwork run`` exit codes and channels for everything that needs no real browser."""

import json
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest
from typer.testing import CliRunner, Result

from mendwork.apps.cli.arguments import parse_input_arguments
from mendwork.apps.cli.main import app
from mendwork.engine.domain.runs import RunId
from mendwork.engine.errors import BrowserUnavailable, RunInputError
from mendwork.engine.ports.browser import BrowserPort
from tests.workflows import example_path

EXAMPLE = str(example_path("download_report"))
INPUTS = [
    "--input",
    "portal_url=http://127.0.0.1:9/index.html",
    "--input",
    "account_email=a@b.test",
]


def invoke(args: list[str], env: dict[str, str] | None = None) -> Result:
    return CliRunner().invoke(app, ["run", *args], env=env or {})


def test_input_arguments_split_at_the_first_equals_sign() -> None:
    assert parse_input_arguments(["a=1", "b=x=y", "c="]) == {"a": "1", "b": "x=y", "c": ""}


def test_malformed_and_repeated_input_arguments_are_all_reported() -> None:
    with pytest.raises(RunInputError) as caught:
        parse_input_arguments(["novalue", "=x", "a=1", "a=2"])

    assert [(issue.path, issue.message) for issue in caught.value.issues] == [
        ("inputs[1]", "--input number 1 must look like name=value"),
        ("inputs[2]", "--input number 2 must look like name=value"),
        ("inputs.a", "is given more than once"),
    ]


def test_a_malformed_input_argument_exits_2(plain_stdout: Callable[[Result], str]) -> None:
    result = invoke([EXAMPLE, "--input", "portal_url"])

    assert result.exit_code == 2
    assert "must look like name=value" in result.stderr


def test_a_missing_required_input_exits_2_before_anything_runs(tmp_path: Path) -> None:
    result = invoke(
        [EXAMPLE, "--artifacts-dir", str(tmp_path), "--input", "portal_url=http://127.0.0.1:9/"],
        {"MENDWORK_SECRET_PORTAL_PASSWORD": "x"},
    )

    assert result.exit_code == 2
    assert "inputs.account_email: is required" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_a_missing_secret_exits_2_and_names_the_variable(tmp_path: Path) -> None:
    result = invoke([EXAMPLE, "--artifacts-dir", str(tmp_path), *INPUTS])

    assert result.exit_code == 2
    assert "set MENDWORK_SECRET_PORTAL_PASSWORD" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_an_invalid_workflow_exits_2_with_its_problems(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("schema_version: 1\nworkflow_id: Broken\n", encoding="utf-8")

    result = invoke([str(broken), "--output", "json"])

    assert result.exit_code == 2
    assert "workflow_id" in result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["exit_code"] == 2


def test_invalid_settings_exit_2(plain_stdout: Callable[[Result], str]) -> None:
    result = invoke([EXAMPLE, *INPUTS], {"MENDWORK_LOG_LEVLE": "DEBUG"})

    assert result.exit_code == 2
    assert "did you mean MENDWORK_LOG_LEVEL?" in result.stderr


class UnlaunchableChromium:
    """Stands in for ChromiumLauncher when Chromium cannot start."""

    def __init__(self, *options: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, *exc: type[BaseException] | BaseException | TracebackType | None
    ) -> None:
        return None

    def session(self, run_id: RunId) -> "FailingSession":
        return FailingSession()


class FailingSession:
    """A session that fails to open, as a launcher does when Chromium is missing."""

    async def __aenter__(self) -> BrowserPort:
        raise BrowserUnavailable("could not launch Chromium")

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def unlaunchable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mendwork.apps.cli.run.ChromiumLauncher", UnlaunchableChromium)


def test_a_browser_that_cannot_launch_exits_3_with_a_run_record(
    tmp_path: Path, unlaunchable: None
) -> None:
    result = invoke(
        [EXAMPLE, "--artifacts-dir", str(tmp_path), "--output", "json", *INPUTS],
        {"MENDWORK_SECRET_PORTAL_PASSWORD": "x"},
    )

    assert result.exit_code == 3
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert [line.get("type") for line in lines[:-1]] == ["run_started", "run_finished"]
    final = lines[-1]
    assert (final["result_version"], final["exit_code"], final["run"]["status"]) == (1, 3, "failed")
    assert final["run"]["error"]["type"] == "BrowserUnavailable"
    [run_directory] = (tmp_path / "runs").iterdir()
    assert json.loads((run_directory / "run.json").read_text())["status"] == "failed"
