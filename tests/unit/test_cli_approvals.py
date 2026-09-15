"""``mendwork show``, ``approve``, and ``reject`` for everything that needs no browser."""

import fcntl
import json
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner, Result

from mendwork.adapters.artifacts_local.records import LOCK_NAME
from mendwork.adapters.audit_fs.log import LOG_NAME
from mendwork.apps.cli.approval_wiring import AUDIT_DIRECTORY
from mendwork.apps.cli.main import app
from mendwork.engine.domain.approvals import ProposalRecord
from mendwork.engine.domain.heals import HealReport, ProposalBox
from mendwork.engine.domain.runs import ArtifactName, Run, RunStatus, StepArtifacts, StepStatus
from mendwork.engine.replay.journal import encode_run, workflow_snapshot
from tests.unit.replay.approval_builders import (
    RUN_ID,
    ledger_workflow,
    paused_run,
    paused_step,
    proposal,
    result,
)

SCREENSHOT = ArtifactName("steps/002_export.png")
BOXED = proposal().model_copy(update={"box": ProposalBox(x=0.6, y=0.3, width=0.12, height=0.05)})


def saved_run(tmp_path: Path, **overrides: object) -> Run:
    """A run paused at its export step, saved with its workflow as a real run would be."""
    data, digest = workflow_snapshot(ledger_workflow(("export", "irreversible")))
    paused = paused_step(BOXED).model_copy(
        update={"artifacts": StepArtifacts(screenshot=SCREENSHOT)}
    )
    values: dict[str, object] = {
        "workflow_sha256": digest,
        "steps": (result("open", 0, StepStatus.SUCCEEDED, action_performed=True), paused),
        "proposals": (ProposalRecord(proposal=BOXED),),
    }
    run = paused_run(**{**values, **overrides})
    directory = tmp_path / "runs" / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_bytes(encode_run(run))
    (directory / "workflow.json").write_bytes(data)
    return run


def invoke(tmp_path: Path, *args: str) -> Result:
    return CliRunner().invoke(app, [*args, "--artifacts-dir", str(tmp_path)], env={})


def record(tmp_path: Path) -> Run:
    return Run.model_validate_json((tmp_path / "runs" / RUN_ID / "run.json").read_bytes())


def audit_lines(tmp_path: Path) -> list[dict[str, object]]:
    log = tmp_path / AUDIT_DIRECTORY / LOG_NAME
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_show_lists_a_pending_proposal_with_its_evidence_and_how_to_decide_it(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    saved_run(tmp_path)

    shown = invoke(tmp_path, "show", RUN_ID)

    out = plain_stdout(shown)
    screenshot = (tmp_path / "runs" / RUN_ID / SCREENSHOT).resolve().as_uri()
    assert shown.exit_code == 0
    assert f"Run {RUN_ID} · ledger v1 · AWAITING APPROVAL" in out
    assert "Proposal export-1 · step 2 export · pending" in out
    assert 'recorded    button "Export ledger"' in out
    assert 'found       button "Export ledger" · rung 2' in out
    assert "numbers     score 0.90 · margin 0.40 · threshold 0.60 · required margin 0.15" in out
    assert "position    x 0.60 · y 0.30 · 0.12 by 0.05 of the page (shown, never matched)" in out
    assert f"screenshot  {screenshot}" in out
    assert f"Approve:  mendwork approve {RUN_ID} export-1" in out
    assert f'Reject:   mendwork reject {RUN_ID} export-1 --reason "why"' in out
    assert "Segment 1 (run): egress allowed no domains" in out


def test_show_reports_json_and_writes_nothing(tmp_path: Path) -> None:
    saved_run(tmp_path, status=RunStatus.RUNNING, finished_at=None, error=None)
    before = (tmp_path / "runs" / RUN_ID / "run.json").read_bytes()

    shown = invoke(tmp_path, "show", RUN_ID, "--output", "json")

    line = json.loads(shown.stdout.splitlines()[-1])
    assert (shown.exit_code, line["exit_code"]) == (0, 0)
    assert line["run"]["status"] == "cancelled"
    assert (tmp_path / "runs" / RUN_ID / "run.json").read_bytes() == before


def test_show_refuses_an_unknown_run_a_malformed_id_and_a_broken_audit_log(
    tmp_path: Path,
) -> None:
    unknown = invoke(tmp_path, "show", RUN_ID)
    malformed = invoke(tmp_path, "show", "yesterday")
    saved_run(tmp_path)
    (tmp_path / AUDIT_DIRECTORY).mkdir()
    (tmp_path / AUDIT_DIRECTORY / LOG_NAME).write_text("not an entry\n", encoding="utf-8")
    broken = invoke(tmp_path, "show", RUN_ID)

    assert (unknown.exit_code, malformed.exit_code, broken.exit_code) == (2, 2, 3)
    assert "UnknownRun" in unknown.stderr
    assert "'yesterday' is not a run id" in malformed.stderr
    assert "AuditLogCorrupt" in broken.stderr


def test_reject_records_the_decision_first_and_ends_the_run_failed(
    tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    saved_run(tmp_path)

    rejected = invoke(tmp_path, "reject", RUN_ID, "export-1", "--reason", "wrong button")
    again = invoke(tmp_path, "reject", RUN_ID, "export-1")

    assert rejected.exit_code == 0
    assert plain_stdout(rejected).splitlines() == [
        f"Rejected proposal export-1 of run {RUN_ID} (audit entry 1).",
        "Reason: wrong button",
        "The run is failed and nothing was acted on. Re-record the step, or fix the page it runs "
        "on, then run the workflow.",
    ]
    assert [(line["kind"], line["reason"]) for line in audit_lines(tmp_path)] == [
        ("proposal_rejected", "wrong button")
    ]
    finished = record(tmp_path)
    assert finished.status is RunStatus.FAILED
    assert finished.proposals[0].decision is not None
    assert again.exit_code == 2
    assert "already rejected (audit entry 1)" in again.stderr


def test_reject_reports_json(tmp_path: Path) -> None:
    saved_run(tmp_path)

    rejected = invoke(tmp_path, "reject", RUN_ID, "export-1", "--output", "json")

    line = json.loads(rejected.stdout.splitlines()[-1])
    assert (line["exit_code"], line["run"]["status"]) == (0, "failed")


def test_a_reason_that_is_not_one_short_line_is_refused_before_anything_is_recorded(
    tmp_path: Path,
) -> None:
    saved_run(tmp_path)

    two_lines = invoke(tmp_path, "reject", RUN_ID, "export-1", "--reason", "one\ntwo")
    too_long = invoke(tmp_path, "reject", RUN_ID, "export-1", "--reason", "x" * 501)

    assert (two_lines.exit_code, too_long.exit_code) == (2, 2)
    assert "--reason must not contain control characters or line breaks" in two_lines.stderr
    assert "--reason must be at most 500 characters" in too_long.stderr
    assert audit_lines(tmp_path) == []
    assert record(tmp_path).status is RunStatus.AWAITING_APPROVAL


def test_approve_refuses_a_run_that_cannot_resume_before_anything_is_recorded(
    tmp_path: Path,
) -> None:
    saved_run(tmp_path, record_version=1)

    refused = invoke(tmp_path, "approve", RUN_ID, "export-1", "--output", "json")

    assert refused.exit_code == 2
    assert "RunNotResumable: run" in refused.stderr
    assert json.loads(refused.stdout.splitlines()[-1])["error"]["type"] == "RunNotResumable"
    assert audit_lines(tmp_path) == []


def test_approve_refuses_an_unknown_proposal_and_a_run_another_process_holds(
    tmp_path: Path,
) -> None:
    saved_run(tmp_path)
    unknown = invoke(tmp_path, "approve", RUN_ID, "export-7")

    with (tmp_path / "runs" / RUN_ID / LOCK_NAME).open("a+b") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        busy = invoke(tmp_path, "approve", RUN_ID, "export-1")
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)

    assert (unknown.exit_code, busy.exit_code) == (2, 2)
    assert "has no proposal export-7" in unknown.stderr
    assert "RunBusy" in busy.stderr
    assert audit_lines(tmp_path) == []


def test_approve_of_a_proposal_already_decided_changes_nothing(tmp_path: Path) -> None:
    saved_run(tmp_path)
    invoke(tmp_path, "reject", RUN_ID, "export-1")
    before = (tmp_path / "runs" / RUN_ID / "run.json").read_bytes()

    refused = invoke(tmp_path, "approve", RUN_ID, "export-1")

    assert refused.exit_code == 2
    assert "ProposalNotPending: proposal export-1 was already rejected" in refused.stderr
    assert (tmp_path / "runs" / RUN_ID / "run.json").read_bytes() == before
    assert len(audit_lines(tmp_path)) == 1


def test_the_heal_report_of_a_saved_paused_step_carries_the_boxed_proposal(tmp_path: Path) -> None:
    saved = saved_run(tmp_path)

    assert saved.steps[1].heal == HealReport(proposal=BOXED)
