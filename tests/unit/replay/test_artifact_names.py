"""Artifact names are sortable, valid, and never let a site choose where a file goes."""

import pytest

from mendwork.engine.domain.runs import parse_artifact_name, parse_run_id
from mendwork.engine.replay.artifact_names import (
    RUN_RECORD,
    TRACE,
    dom_snapshot_name,
    download_name,
    safe_filename,
    screenshot_name,
    step_label,
)


def test_step_evidence_is_named_by_one_based_position_and_id() -> None:
    assert step_label(3, "sign_in") == "004_sign_in"
    assert screenshot_name(3, "sign_in") == "steps/004_sign_in.png"
    assert dom_snapshot_name(3, "sign_in") == "failure/004_sign_in.dom.html"
    assert (RUN_RECORD, TRACE) == ("run.json", "failure/trace.zip")


@pytest.mark.parametrize(
    ("suggested", "safe"),
    [
        ("shipments_2026-02-10_to_2026-04-20.csv", "shipments_2026-02-10_to_2026-04-20.csv"),
        ("../../.bashrc", "bashrc"),
        ("C:\\Users\\me\\report.pdf", "report.pdf"),
        ("résumé final.pdf", "r_sum_final.pdf"),
        ("...", "download"),
        ("", "download"),
    ],
)
def test_suggested_filenames_become_one_safe_segment(suggested: str, safe: str) -> None:
    assert safe_filename(suggested) == safe


def test_a_taken_download_name_is_prefixed_with_its_step() -> None:
    assert download_name("report.csv", 0, "export", set()) == "downloads/report.csv"
    assert (
        download_name("report.csv", 4, "export", {"downloads/report.csv"})
        == "downloads/005_export_report.csv"
    )


@pytest.mark.parametrize(
    "name", ["", "/etc/passwd", "../run.json", "steps/../../x", "a/b/c/d/e", ".hidden", "steps/.x"]
)
def test_names_that_could_escape_or_hide_are_refused(name: str) -> None:
    with pytest.raises(ValueError, match="artifact name"):
        parse_artifact_name(name)


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        "20260911T141502Z-7c1e09a",
        "20260911T141502Z-7C1E09AB",
        "../x",
        "20260911T141502Z-7c1e09ab\n",
    ],
)
def test_malformed_run_ids_are_refused(run_id: str) -> None:
    with pytest.raises(ValueError, match="run id"):
        parse_run_id(run_id)
