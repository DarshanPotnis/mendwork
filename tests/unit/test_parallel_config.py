"""make check, make check-all, and CI run tests in the same parallel configuration (ADR 0012).

Parallelism lives in the Makefile, not in pyproject's addopts: the coverage ratchet runs pytest in
a subprocess of its own, and addopts would give that subprocess workers of its own inside a worker.
"""

import re
import tomllib
from pathlib import Path
from typing import Final

REPO: Final = Path(__file__).resolve().parents[2]
PARALLEL: Final = "$(PYTEST_PARALLEL)"


def recipe(makefile: str, target: str) -> str:
    """The first command line of a Makefile target."""
    match = re.search(rf"^{re.escape(target)}:\n\t(.+)$", makefile, flags=re.MULTILINE)
    assert match is not None, f"no {target} target"
    return match.group(1)


def test_both_test_targets_use_four_workers_and_whole_files() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert re.search(r"^PYTEST_WORKERS \?= 4$", makefile, flags=re.MULTILINE)
    assert re.search(
        r"^PYTEST_PARALLEL := -n \$\(PYTEST_WORKERS\) --dist loadfile$",
        makefile,
        flags=re.MULTILINE,
    )
    assert PARALLEL in recipe(makefile, "test")
    assert PARALLEL in recipe(makefile, "test-all")


def test_pytest_itself_is_not_configured_to_run_in_parallel() -> None:
    pytest_options = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "pytest"
    ]["ini_options"]
    addopts = " ".join(pytest_options.get("addopts", []))

    assert not re.search(r"(^|\s)(-n|--numprocesses|--dist|-d)(\s|=|$)", addopts)


def test_ci_runs_the_same_make_target() -> None:
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert re.search(r"^\s+run: make check-all$", workflow, flags=re.MULTILINE)
    assert "pytest" not in workflow
