"""``mendwork run`` and the workflow store: the version a file runs, what it says, and its report.

Chromium never starts: the launcher fails the way a missing browser does, so each run ends with a
record, and the store and the report are what these tests look at (ADR 0013).
"""

import json
from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner, Result

from mendwork.apps.cli.main import app
from tests.unit.cli_store import publish
from tests.unit.patching.builders import with_intent
from tests.unit.test_cli_run import EXAMPLE, INPUTS, UnlaunchableChromium
from tests.workflows import load_example

ENV: Final = {
    "MENDWORK_SECRET_PORTAL_PASSWORD": "x",
    "MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS": '["127.0.0.1:9"]',
}


@pytest.fixture(autouse=True)
def unlaunchable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mendwork.apps.cli.run.ChromiumLauncher", UnlaunchableChromium)


def invoke(tmp_path: Path, *args: str) -> Result:
    return CliRunner().invoke(
        app,
        [
            "run",
            EXAMPLE,
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--store-dir",
            str(tmp_path / "store"),
            "--output",
            "json",
            *INPUTS,
            *args,
        ],
        env=ENV,
    )


def test_a_first_run_stores_its_file_as_version_1_and_writes_its_report_even_when_it_fails(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"

    first = invoke(tmp_path)
    again = invoke(tmp_path)

    final = json.loads(first.stdout.splitlines()[-1])
    assert first.exit_code == 3
    assert (
        f"Stored {EXAMPLE} as download_report v1 in {store}; heals from its runs are saved there."
        in first.stderr
    )
    source = final["run"]["source"]
    assert (source["path"], source["stored_version"], source["saves_heals"]) == (EXAMPLE, 1, True)
    report = Path(final["report"])
    assert report == tmp_path / "artifacts" / "runs" / final["run"]["run_id"] / "report.html"
    assert report.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert "Stored" not in again.stderr
    assert sorted(path.name for path in (store / "download_report").iterdir()) == ["v0001.yaml"]


def test_exact_runs_the_file_as_written_and_leaves_the_store_alone(tmp_path: Path) -> None:
    result = invoke(tmp_path, "--exact")

    final = json.loads(result.stdout.splitlines()[-1])
    assert final["run"]["source"]["not_saved"] == "exact"
    assert "Stored" not in result.stderr
    assert not (tmp_path / "store").exists()


def test_a_file_that_differs_from_every_stored_version_runs_as_written_and_says_how_to_import_it(
    tmp_path: Path,
) -> None:
    example = load_example("download_report")
    opening = example.steps[0].id
    stored = example.model_copy(
        update={"steps": with_intent(example, opening, "Open the portal's front page").steps}
    )
    publish(tmp_path / "store", stored)

    result = invoke(tmp_path)

    final = json.loads(result.stdout.splitlines()[-1])
    assert (final["run"]["workflow_version"], final["run"]["source"]["not_saved"]) == (
        1,
        "file_differs",
    )
    assert (
        f"{EXAMPLE} differs from every stored version of download_report, so it runs as written "
        f"and its heals are not saved; mendwork import {EXAMPLE} makes it the latest version."
    ) in result.stderr
