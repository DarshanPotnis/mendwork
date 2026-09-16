"""The logic that decides is covered by unit tests alone, never only by browser tests.

Rung 0, identity, consensus, checkpoint evaluation, pre-action checks, retries, secret
scrubbing, recording, and the heal ladder live in engine/replay, engine/verification,
engine/safety, engine/recording, and engine/healing. This test runs the
engine's fake-port unit tests (and the logging tests that exercise redaction) under coverage
in a separate process, and fails if any file in those packages is below 90% line coverage
there. Those tests are a subset of make check's fast suite, so a file that passes here also
passes under the fast suite. See ADR 0007.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

REPO: Final = Path(__file__).resolve().parents[2]
RATCHET_PERCENT: Final = 90.0
PACKAGES: Final = (
    "src/mendwork/engine/replay/",
    "src/mendwork/engine/verification/",
    "src/mendwork/engine/safety/",
    "src/mendwork/engine/recording/",
    "src/mendwork/engine/healing/",
    "src/mendwork/engine/patching/",
    "src/mendwork/engine/reporting/",
    "src/mendwork/engine/benchmark/",
)
UNIT_TESTS: Final = (
    "tests/unit/benchmark",
    "tests/unit/replay",
    "tests/unit/recording",
    "tests/unit/safety",
    "tests/unit/healing",
    "tests/unit/patching",
    "tests/unit/reporting",
    "tests/unit/test_observability.py",
)


def _environment() -> dict[str, str]:
    # A coverage run in the parent process must not leak into the process being measured.
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("COV_CORE_", "COVERAGE_"))
    }


def _line_coverage(workdir: Path) -> dict[str, float | None]:
    """Line coverage per file under the unit tests; None for files with no statements."""
    data = workdir / ".coverage"
    report = workdir / "coverage.json"
    measured = subprocess.run(  # noqa: S603 - a fixed command line on our own interpreter
        [sys.executable, "-m", "coverage", "run", f"--data-file={data}", "-m", "pytest",
         *UNIT_TESTS, "-q", "-p", "no:cacheprovider"],
        cwd=REPO, env=_environment(), capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert measured.returncode == 0, measured.stdout[-4000:] + measured.stderr[-4000:]
    subprocess.run(  # noqa: S603 - a fixed command line on our own interpreter
        [sys.executable, "-m", "coverage", "json", f"--data-file={data}", "-o", str(report), "-q"],
        cwd=REPO, env=_environment(), capture_output=True, check=True,
    )  # fmt: skip
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    coverage: dict[str, float | None] = {}
    for name, file in files.items():
        summary = file["summary"]
        if name.startswith(PACKAGES):
            statements = summary["num_statements"]
            coverage[name] = summary["covered_lines"] * 100 / statements if statements else None
    return coverage


def test_every_deciding_engine_file_is_covered_by_unit_tests_alone(tmp_path: Path) -> None:
    coverage = _line_coverage(tmp_path)
    on_disk = {
        str(path.relative_to(REPO))
        for package in PACKAGES
        for path in (REPO / package).glob("*.py")
    }

    assert on_disk - set(coverage) == set(), "files the unit tests never measured"
    assert len(on_disk) >= 20
    below = {
        name: round(percent, 2)
        for name, percent in sorted(coverage.items())
        if percent is not None and percent < RATCHET_PERCENT
    }
    assert below == {}, f"below {RATCHET_PERCENT}% line coverage from unit tests alone"
