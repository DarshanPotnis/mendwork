"""``mendwork history``, ``diff``, and ``rollback`` on a workflow store on disk (ADR 0013)."""

from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner, Result

from mendwork.apps.cli.main import app
from mendwork.engine.domain.changes import Rollback
from mendwork.engine.domain.enums import ChangeKind
from mendwork.engine.domain.lineage import edit_version
from mendwork.engine.patching.diff import LEVEL_SEPARATOR
from mendwork.engine.patching.words import moment
from tests.heal_changes import HEAL_RUN
from tests.unit.cli_store import CLOCK, PENDING_RUN, healed, history_of, pend, publish, raced_store
from tests.unit.patching.builders import with_intent

AFTER_N: Final = {"MENDWORK_PATCH_PROMOTION": "after_n_successes"}
HEALED: Final = "Step 2 export healed at rung 2, verified strongly"
WEAK: Final = (
    "1 of 2 steps is weakly verified or not verified: on those, a heal proves where the page went "
    "or what a field holds, not which element was used."
)


def invoke(root: Path, *args: str, env: dict[str, str] | None = None) -> Result:
    return CliRunner().invoke(app, [*args, "--store-dir", str(root)], env=env or {})


def element_rows(before: str, after: str) -> list[str]:
    """The ledger export button's rows when its name went from ``before`` to ``after``."""
    rows = [
        "  Element            {v1:<30}  {v2}",
        "  Kind               a button                        a button",
        f"  Name               {before:<30}  {after:<30}  changed",
        '  Text               "Export ledger"                 "Export ledger"',
        '  Test id            "ledger-export"                 "ledger-export"',
        '  Id on the page     "export-ledger"                 "export-ledger"',
        '  Button type        "button"                        "button"',
        '  Nearby text        "Quarterly ledger"              "Quarterly ledger"',
        "  Place in the page  main > article > div > button   main > article > div > button",
        '  Found by           by its test id "ledger-export"  by its test id "ledger-export"',
    ]
    return [row.replace(" > ", LEVEL_SEPARATOR) for row in rows]


def test_history_lists_versions_newest_first_with_step_strengths_and_pending_patches(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    first, second = healed(root)
    pend(root, second)

    shown = invoke(root, "history", "ledger", env=AFTER_N)

    assert shown.exit_code == 0
    assert plain_stdout(shown).splitlines() == [
        f"ledger · 2 versions in {root}",
        f"  v2  {moment(second.created_at)}  {HEALED} · run {HEAL_RUN}",
        f"  v1  {moment(first.created_at)}  First version",
        "",
        "Checks in v2:",
        "  1  open    no checks     not verified",
        "  2  export  text_present  strong",
        WEAK,
        "",
        "Pending patches, each saved as a version once 3 succeeded runs verify it:",
        f'  Step 2 export: a button named "Export", verified in 1 of 3 runs ({PENDING_RUN})',
    ]


def test_history_under_immediate_promotion_says_pending_patches_are_not_tried(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    _, second = healed(root)
    without = plain_stdout(invoke(root, "history", "ledger")).splitlines()
    pend(root, second)

    shown = plain_stdout(invoke(root, "history", "ledger")).splitlines()

    assert without[-1] == WEAK
    assert shown[-2:] == [
        "Pending patches, not tried while MENDWORK_PATCH_PROMOTION is immediate:",
        f'  Step 2 export: a button named "Export", verified in 1 of 3 runs ({PENDING_RUN})',
    ]


def test_history_refuses_an_invalid_id_an_unknown_workflow_and_an_unreadable_store(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    invalid = invoke(root, "history", "Not An Id")
    unknown = invoke(root, "history", "ledger")
    (root / "ledger").mkdir(parents=True)
    (root / "ledger" / "v0001.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    unreadable = invoke(root, "history", "ledger")

    assert (invalid.exit_code, unknown.exit_code, unreadable.exit_code) == (2, 2, 3)
    assert "'Not An Id' is not a workflow id" in invalid.stderr
    assert f"{root} has no versions of ledger." in unknown.stderr
    assert "WorkflowValidationError" in unreadable.stderr


def test_diff_shows_why_a_heal_changed_a_step_and_its_element_on_each_side(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    healed(root)

    shown = invoke(root, "diff", "ledger", "1")

    assert shown.exit_code == 0
    assert plain_stdout(shown).splitlines() == [
        "ledger v1 → v2 · 1 step changed, 1 unchanged",
        "",
        "Step 2 export · \"Click the 'Export ledger' button\"",
        "  Why: v2: Repaired automatically by similarity scoring (rung 2, no AI model) and "
        'confirmed by the step\'s check: the text "Ledger exported" appeared. That check is '
        "strong: it shows the right element was used.",
        "       Similarity 0.85 (0.60 needed), 0.63 ahead of the next closest element "
        "(0.15 needed).",
        *(
            row.format(v1="v1", v2="v2")
            for row in element_rows('"Export ledger"', '"Share ledger"')
        ),
        "",
        "Versions after v1:",
        f"  v2  {HEALED}",
    ]


def test_diff_of_a_version_with_itself_and_of_versions_that_are_not_stored(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    unknown_workflow = invoke(root, "diff", "ledger", "1")
    healed(root)

    same = invoke(root, "diff", "ledger", "v1", "v1")
    unknown = invoke(root, "diff", "ledger", "1", "7")
    malformed = invoke(root, "diff", "ledger", "first")

    assert (same.exit_code, unknown.exit_code, malformed.exit_code) == (0, 2, 2)
    assert plain_stdout(same).splitlines() == [
        "ledger v1 → v1 · 0 steps changed, 2 unchanged",
        "Both versions have the same steps, inputs, and secrets.",
    ]
    assert "ledger has no v7; its versions are v1 to v2." in unknown.stderr
    assert "'first' is not a version number, such as 2 or v2" in malformed.stderr
    assert unknown_workflow.exit_code == 2
    assert "the workflow store has no versions of ledger." in unknown_workflow.stderr


def test_rollback_lists_what_it_undoes_creates_a_new_version_and_deletes_nothing(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    first, second = healed(root)
    pend(root, second)
    files = {path.name: path.read_bytes() for path in (root / "ledger").iterdir()}

    rolled = invoke(root, "rollback", "ledger", "--to", "v1", "--reason", "the wrong button")

    assert rolled.exit_code == 0
    assert plain_stdout(rolled).splitlines() == [
        "Rolling back ledger to v1 creates v3 with the content of v1. "
        "Nothing is deleted. It undoes:",
        f"  v2  {moment(second.created_at)}  {HEALED}",
        "A heal it undoes is not saved again automatically, even when a later run verifies it.",
        "",
        "Steps that change from v2:",
        "Step 2 export · \"Click the 'Export ledger' button\"",
        *(
            row.format(v1="v2", v2="v3")
            for row in element_rows('"Share ledger"', '"Export ledger"')
        ),
        "",
        "Pending patches on those steps stop matching and will never become versions:",
        f'  Step 2 export: a button named "Export", verified in 1 run ({PENDING_RUN})',
        "Rolled back ledger to v1 as v3. To restore v2: mendwork rollback ledger --to 2",
    ]
    history = history_of(root)
    assert [item.version for item in history] == [1, 2, 3]
    assert (history[2].parent_version, history[2].content) == (2, first.content)
    assert history[2].change == Rollback(
        kind=ChangeKind.ROLLBACK, restored_version=1, reason="the wrong button"
    )
    assert {name: (root / "ledger" / name).read_bytes() for name in files} == files


def test_a_rollback_also_undoes_versions_published_after_the_run_a_person_examined(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    """A person examined a run of v2 and rolls back to v1; v3 was published since, so it is
    undone too, listed first, and the new version follows v3 rather than v2."""
    root = tmp_path / "store"
    first, second = healed(root)
    third = edit_version(
        second,
        with_intent(second, "open", "Open the ledger page"),
        summary="clearer intent",
        clock=CLOCK,
    )
    publish(root, third)

    rolled = invoke(root, "rollback", "ledger", "--to", "1")

    out = plain_stdout(rolled).splitlines()
    assert rolled.exit_code == 0
    assert out[:3] == [
        "Rolling back ledger to v1 creates v4 with the content of v1. "
        "Nothing is deleted. It undoes:",
        f"  v3  {moment(third.created_at)}  Edited by hand: clearer intent",
        f"  v2  {moment(second.created_at)}  {HEALED}",
    ]
    assert "Steps that change from v3:" in out
    newest = history_of(root)[-1]
    assert (newest.version, newest.parent_version, newest.content) == (4, 3, first.content)
    assert newest.change == Rollback(
        kind=ChangeKind.ROLLBACK, restored_version=1, reason="no reason given"
    )


def test_rollback_refuses_the_latest_version_an_unknown_one_and_a_reason_of_two_lines(
    tmp_path: Path,
) -> None:
    root = tmp_path / "store"
    healed(root)

    latest = invoke(root, "rollback", "ledger", "--to", "2")
    unknown = invoke(root, "rollback", "ledger", "--to", "9")
    two_lines = invoke(root, "rollback", "ledger", "--to", "1", "--reason", "one\ntwo")

    assert (latest.exit_code, unknown.exit_code, two_lines.exit_code) == (2, 2, 2)
    assert (
        "Nothing was rolled back: can only roll back to a version before v2, not v2."
        in latest.stderr
    )
    assert "Nothing was rolled back: ledger has no v9; its versions are v1 to v2." in unknown.stderr
    assert two_lines.stderr.startswith("--reason ")
    assert [item.version for item in history_of(root)] == [1, 2]


def test_a_rollback_another_process_publishes_ahead_of_stores_nothing_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    healed(root)
    monkeypatch.setattr("mendwork.apps.cli.rollback.workflow_store", raced_store)

    rolled = invoke(root, "rollback", "ledger", "--to", "1")

    assert rolled.exit_code == 1
    assert "Nothing was rolled back: another process published ledger v3 first." in rolled.stderr
    assert [item.version for item in history_of(root)] == [1, 2]
