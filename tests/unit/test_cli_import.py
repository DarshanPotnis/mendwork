"""``mendwork import``: what it says it will do, both answers to its question, and --yes."""

from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner, Result

from mendwork.apps.cli.main import app
from mendwork.engine.domain.changes import ManualEdit
from mendwork.engine.domain.enums import ChangeKind
from mendwork.engine.domain.patches import NotSavedReason
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.sources import choose_version
from tests.unit.cli_store import CODEC, PENDING_RUN, healed, history_of, pend, raced_store
from tests.unit.patching.builders import ledger_steps, ledger_version, with_intent

AFTERWARDS: Final = (
    "Afterwards, runs of this file run v3 and save their heals; a file with an earlier version's "
    "content runs as written and saves nothing."
)


def invoke(root: Path, *args: str, answer: str | None = None) -> Result:
    return CliRunner().invoke(
        app, ["import", *args, "--store-dir", str(root)], input=answer, env={}
    )


def written(tmp_path: Path, version: WorkflowVersion) -> Path:
    path = tmp_path / "ledger.yaml"
    path.write_bytes(CODEC.encode(version))
    return path


def edited(first: WorkflowVersion) -> WorkflowVersion:
    """The ledger's v1 with its export step's intent edited by hand."""
    return first.model_copy(
        update={"steps": with_intent(first, "export", "Export the ledger").steps}
    )


def test_import_says_what_it_will_do_and_imports_nothing_when_the_answer_is_no(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    first, second = healed(root)
    pend(root, second)
    path = written(tmp_path, edited(first))

    declined = invoke(root, str(path), answer="n\n")

    out = plain_stdout(declined).splitlines()
    assert declined.exit_code == 1
    assert out[:5] == [
        f"Importing {path} creates ledger v3, edited by hand from v2, the latest stored version "
        "(Step 2 export healed at rung 2, verified strongly).",
        "",
        "Steps that differ from v2:",
        'Step 2 export · "Export the ledger"',
        '  Intent: "Click the \'Export ledger\' button" → "Export the ledger"',
    ]
    assert (
        '  Name               "Share ledger"                  "Export ledger"                 '
        "changed"
    ) in out
    assert out[-5:] == [
        "",
        "Pending patches on those steps will stop matching and never become versions:",
        f'  Step 2 export: a button named "Export", verified in 1 run ({PENDING_RUN})',
        AFTERWARDS,
        "Import? [y/N] Nothing was imported.",
    ]
    assert [item.version for item in history_of(root)] == [1, 2]


def test_the_end_of_input_is_not_a_yes(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    first, _ = healed(root)
    path = written(tmp_path, edited(first))

    ended = invoke(root, str(path), answer="")

    assert ended.exit_code == 1
    assert plain_stdout(ended).splitlines()[-3:] == [
        AFTERWARDS,
        "Import? [y/N] ",
        "Nothing was imported.",
    ]
    assert [item.version for item in history_of(root)] == [1, 2]


def test_a_yes_imports_the_file_as_the_latest_version_whose_runs_save_heals(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    first, _ = healed(root)
    (tmp_path / "old").mkdir()
    old = written(tmp_path / "old", first)
    hand_edited = edited(first)
    path = written(tmp_path, hand_edited)

    confirmed = invoke(root, str(path), answer="yes\n")

    assert confirmed.exit_code == 0
    assert plain_stdout(confirmed).splitlines()[-1] == (
        f"Import? [y/N] Imported {path} as ledger v3."
    )
    history = history_of(root)
    assert [item.version for item in history] == [1, 2, 3]
    assert (history[2].parent_version, history[2].content) == (2, hand_edited.content)
    assert history[2].change == ManualEdit(
        kind=ChangeKind.MANUAL_EDIT, summary="Imported from ledger.yaml"
    )
    assert choose_version(hand_edited, str(path), history, exact=False).source.saves_heals
    assert old is not None
    older = choose_version(first, str(old), history, exact=False)
    assert (older.version, older.source.not_saved) == (first, NotSavedReason.NEWER_IMPORT)


def test_yes_answers_for_scripts_without_asking(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    first, _ = healed(root)
    path = written(tmp_path, edited(first))

    scripted = invoke(root, str(path), "--yes", "--summary", "Clearer export intent")

    out = plain_stdout(scripted)
    assert scripted.exit_code == 0
    assert "Import?" not in out
    assert out.splitlines()[-1] == f"Imported {path} as ledger v3."
    assert history_of(root)[2].change == ManualEdit(
        kind=ChangeKind.MANUAL_EDIT, summary="Clearer export intent"
    )


def test_importing_into_an_empty_store_stores_the_first_version(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    path = written(tmp_path, ledger_version())

    imported = invoke(root, str(path), "--yes")

    assert imported.exit_code == 0
    assert plain_stdout(imported).splitlines() == [
        f"Importing {path} stores it as ledger v1, the first version of ledger in {root}. Runs "
        "of the file then save their heals.",
        f"Imported {path} as ledger v1.",
    ]
    assert history_of(root) == (ledger_version(),)


def test_a_file_that_is_already_the_latest_version_imports_nothing(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    _, second = healed(root)
    path = written(tmp_path, second)

    unchanged = invoke(root, str(path))

    assert unchanged.exit_code == 0
    assert plain_stdout(unchanged).splitlines() == [
        f"{path} is already ledger v2, the latest stored version; nothing was imported."
    ]


def test_a_file_with_an_earlier_version_s_content_points_to_a_rollback(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    root = tmp_path / "store"
    first, _ = healed(root)
    path = written(tmp_path, first)

    declined = invoke(root, str(path), answer="n\n")

    out = plain_stdout(declined).splitlines()
    assert declined.exit_code == 1
    assert out[1] == (
        "The file has the content of v1. mendwork rollback ledger --to 1 restores that as a "
        "rollback instead, which also keeps the heals it undoes from being saved again."
    )
    assert "No pending patches are affected." in out


def test_a_file_whose_step_ids_differ_an_invalid_file_and_a_lost_race_import_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    first, _ = healed(root)
    steps = ledger_steps()
    steps[1]["id"] = "share"
    renamed = written(tmp_path, ledger_version(steps=steps))
    broken = tmp_path / "broken.yaml"
    broken.write_text("schema_version: 1\nworkflow_id: ledger\n", encoding="utf-8")

    refused = invoke(root, str(renamed), "--yes")
    invalid = invoke(root, str(broken), "--yes")
    monkeypatch.setattr("mendwork.apps.cli.import_workflow.workflow_store", raced_store)
    raced = invoke(root, str(written(tmp_path, edited(first))), "--yes")

    assert (refused.exit_code, invalid.exit_code, raced.exit_code) == (2, 2, 1)
    assert (
        "Nothing was imported: ledger v2 is the latest stored version, and every version keeps "
        "its step ids." in refused.stderr
    )
    assert "steps" in invalid.stderr
    assert "Nothing was imported: another process published ledger v3 first." in raced.stderr
    assert [item.version for item in history_of(root)] == [1, 2]
