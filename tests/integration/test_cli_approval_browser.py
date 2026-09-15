"""``mendwork run``, ``show``, ``approve``, and ``reject`` end to end, each with its own Chromium.

On the chaos portal, a renamed download button is healed at an irreversible step, so the run stops
for approval. Approving resumes it in a new browser and downloads the report; rejecting ends it
failed. On the JS-free fixture site, the same flow proves that no secret reaches anything it leaves
behind: command output and logs, run records, saved workflows, the audit log, or a trace.
"""

import json
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner, Result

from benchmarks.chaos.local_egress import local_policy
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.main import app
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.domain.documents import parse_workflow_document
from mendwork.engine.domain.runs import Run, RunStatus
from tests.integration.portal import DEMO_EMAIL, DEMO_PASSWORD
from tests.secret_search import every_file, leaks
from tests.workflows import REPO_ROOT, Document

pytestmark = [pytest.mark.browser, pytest.mark.slow]

Mendwork = Callable[[list[str], dict[str, str], Sequence[str]], Result]
APPROVAL_WORKFLOW: Final = (
    REPO_ROOT / "tests" / "fixtures" / "workflows" / "download_report_approval.yaml"
)
CHAOS: Final = "?seed=12&only=synonym_rename"
"""Renames the reports page's Download CSV button (heal_pairs.json)."""
DOWNLOAD_PROPOSAL: Final = "download_csv-1"
FIXTURE_SITE: Final = REPO_ROOT / "tests" / "fixtures" / "sites" / "secret_login"
LEAK_SECRET: Final = "Zq7-leak/probe &4421 ü"
EXPORT_PROPOSAL: Final = "export-1"


@pytest.fixture
def mendwork() -> Mendwork:
    """Run a ``mendwork`` command in-process; the local servers named are all it may reach."""

    def invoke(arguments: list[str], env: dict[str, str], servers: Sequence[str]) -> Result:
        origins = [exception.origin for exception in local_policy(servers).loopback_exceptions]
        egress = {"MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS": json.dumps(origins)}
        return CliRunner().invoke(app, arguments, env={**egress, **env})

    return invoke


@pytest.fixture(scope="module")
def fixture_site() -> Iterator[str]:
    with PortalServer(FIXTURE_SITE, host="127.0.0.1", port=0) as server:
        yield server.url


def only_run(artifacts: Path) -> str:
    (directory,) = (artifacts / "runs").iterdir()
    return directory.name


def record(artifacts: Path, run_id: str) -> Run:
    return Run.model_validate_json((artifacts / "runs" / run_id / "run.json").read_bytes())


def audit_entries(artifacts: Path) -> list[dict[str, object]]:
    log = artifacts / "audit" / "audit.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def pause_download(mendwork: Mendwork, portal_url: str, artifacts: Path) -> tuple[Result, str]:
    paused = mendwork(
        [
            "run",
            str(APPROVAL_WORKFLOW),
            "--artifacts-dir",
            str(artifacts),
            "--input",
            f"portal_url={portal_url}index.html{CHAOS}",
            "--input",
            f"account_email={DEMO_EMAIL}",
        ],
        {"MENDWORK_SECRET_PORTAL_PASSWORD": DEMO_PASSWORD},
        [portal_url],
    )
    assert paused.exit_code == 4, paused.stdout + paused.stderr
    return paused, only_run(artifacts)


def test_an_approved_download_resumes_in_a_new_browser_and_downloads_the_report(
    mendwork: Mendwork, portal_url: str, tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    artifacts = tmp_path / "artifacts"
    paused, run_id = pause_download(mendwork, portal_url, artifacts)

    shown = mendwork(["show", run_id, "--artifacts-dir", str(artifacts)], {}, [portal_url])
    approved = mendwork(
        ["approve", run_id, DOWNLOAD_PROPOSAL, "--artifacts-dir", str(artifacts)],
        {"MENDWORK_SECRET_PORTAL_PASSWORD": DEMO_PASSWORD},
        [portal_url],
    )

    assert f"Approve:  mendwork approve {run_id} {DOWNLOAD_PROPOSAL}" in plain_stdout(paused)
    assert shown.exit_code == 0, shown.stderr
    assert f"Proposal {DOWNLOAD_PROPOSAL} · step 9 download_csv · pending" in plain_stdout(shown)
    assert approved.exit_code == 0, approved.stdout + approved.stderr
    output = plain_stdout(approved)
    assert f"Run {run_id} resumed" in output
    assert (
        f"Proposal {DOWNLOAD_PROPOSAL}: acted on the approved element, and its checkpoints passed"
        in output
    )
    finished = record(artifacts, run_id)
    assert finished.status is RunStatus.SUCCEEDED
    download = finished.steps[8].artifacts.download
    assert download is not None
    rows = (artifacts / "runs" / run_id / download).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 + 14
    assert [(entry["kind"], entry["proposal_id"]) for entry in audit_entries(artifacts)] == [
        ("proposal_approved", DOWNLOAD_PROPOSAL)
    ]
    steps = artifacts / "runs" / run_id / "steps"
    assert (steps / "009_download_csv.png").is_file()
    assert (steps / "009_download_csv.segment2.png").is_file()


def test_a_rejected_download_ends_the_run_failed_and_downloads_nothing(
    mendwork: Mendwork, portal_url: str, tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    artifacts = tmp_path / "artifacts"
    _, run_id = pause_download(mendwork, portal_url, artifacts)

    rejected = mendwork(
        [
            "reject",
            run_id,
            DOWNLOAD_PROPOSAL,
            "--reason",
            "the renamed button needs a new recording",
            "--artifacts-dir",
            str(artifacts),
        ],
        {},
        [portal_url],
    )
    again = mendwork(
        ["approve", run_id, DOWNLOAD_PROPOSAL, "--artifacts-dir", str(artifacts)],
        {"MENDWORK_SECRET_PORTAL_PASSWORD": DEMO_PASSWORD},
        [portal_url],
    )

    assert rejected.exit_code == 0, rejected.stderr
    assert plain_stdout(rejected).splitlines()[:2] == [
        f"Rejected proposal {DOWNLOAD_PROPOSAL} of run {run_id} (audit entry 1).",
        "Reason: the renamed button needs a new recording",
    ]
    finished = record(artifacts, run_id)
    assert finished.status is RunStatus.FAILED
    assert finished.error is not None
    assert finished.error.type == "ProposalRejected"
    assert not (artifacts / "runs" / run_id / "downloads").exists()
    assert again.exit_code == 2
    assert "already rejected" in again.stderr


def approval_leak_workflow() -> Document:
    """Sign in with a secret, then export at an irreversible step whose test id went stale."""

    def control(tag: str, name: str, test_id: str, **extra: object) -> dict[str, object]:
        return {
            "tag": tag,
            "accessible_name": name,
            "structural_path": f"main > form > {tag}",
            "selectors": [{"strategy": "test_id", "value": test_id}],
            **extra,
        }

    return {
        "schema_version": 1,
        "workflow_id": "approval_leak_probe",
        "version": 1,
        "created_at": "2026-09-14T00:00:00Z",
        "inputs": [{"name": "site_url", "kind": "url"}],
        "secrets": ["site_password"],
        "steps": [
            {
                "id": "open",
                "intent": "Open the sign-in page",
                "action": "navigate",
                "risk": "safe",
                "value": {"kind": "input", "name": "site_url"},
            },
            {
                "id": "fill_password",
                "intent": "Fill the 'Password' field",
                "action": "fill",
                "risk": "caution",
                "target": control("input", "Password", "password", attributes={"type": "password"}),
                "value": {"kind": "secret", "name": "site_password"},
                "checkpoints": [{"kind": "field_has_value"}],
            },
            {
                "id": "sign_in",
                "intent": "Click the 'Sign in' button",
                "action": "click",
                "risk": "caution",
                "target": control(
                    "button", "Sign in", "sign-in", role="button", attributes={"type": "submit"}
                ),
                "checkpoints": [
                    {
                        "kind": "url_matches",
                        "mode": "regex",
                        "pattern": r"https?://[^?#]+/app\.html(?:[?#].*)?",
                    }
                ],
            },
            {
                "id": "export",
                "intent": "Click the 'Export' button",
                "action": "click",
                "risk": "irreversible",
                "target": {
                    "tag": "button",
                    "role": "button",
                    "accessible_name": "Export",
                    "text": "Export",
                    "attributes": {"id": "export", "type": "button"},
                    "structural_path": "main > button",
                    "selectors": [{"strategy": "test_id", "value": "export-report"}],
                },
                "checkpoints": [
                    {"kind": "text_present", "text": "Export complete", "timeout_ms": 400}
                ],
            },
        ],
    }


def test_no_secret_reaches_anything_an_approval_or_a_rejection_leaves_behind(
    mendwork: Mendwork, fixture_site: str, tmp_path: Path
) -> None:
    workflow = tmp_path / "approval_leak_probe.yaml"
    workflow.write_bytes(
        WorkflowYamlCodec(max_bytes=1 << 20).encode(
            parse_workflow_document(approval_leak_workflow())
        )
    )
    env = {"MENDWORK_SECRET_SITE_PASSWORD": LEAK_SECRET, "MENDWORK_LOG_LEVEL": "DEBUG"}
    servers = [fixture_site]

    def pause(artifacts: Path) -> tuple[Result, str]:
        run = mendwork(
            [
                "run",
                str(workflow),
                "--output",
                "json",
                "--artifacts-dir",
                str(artifacts),
                "--input",
                f"site_url={fixture_site}index.html",
            ],
            env,
            servers,
        )
        assert run.exit_code == 4, run.stdout + run.stderr
        return run, only_run(artifacts)

    approved_artifacts = tmp_path / "approved"
    rejected_artifacts = tmp_path / "rejected"
    first, approved_run = pause(approved_artifacts)
    shown = mendwork(
        ["show", approved_run, "--artifacts-dir", str(approved_artifacts)], env, servers
    )
    approved = mendwork(
        ["approve", approved_run, EXPORT_PROPOSAL, "--artifacts-dir", str(approved_artifacts)],
        env,
        servers,
    )
    second, rejected_run = pause(rejected_artifacts)
    rejected = mendwork(
        [
            "reject",
            rejected_run,
            EXPORT_PROPOSAL,
            "--reason",
            f"typed {LEAK_SECRET} by mistake",
            "--artifacts-dir",
            str(rejected_artifacts),
            "--output",
            "json",
        ],
        env,
        servers,
    )

    assert (shown.exit_code, approved.exit_code, rejected.exit_code) == (0, 4, 0)
    assert record(approved_artifacts, approved_run).status is RunStatus.NEEDS_REVIEW
    assert (approved_artifacts / "runs" / approved_run / "failure" / "trace.segment2.zip").is_file()
    (reason,) = [entry["reason"] for entry in audit_entries(rejected_artifacts)]
    assert reason == "typed [REDACTED] by mistake"
    searched = [
        (f"{name} {stream}", text.encode())
        for name, result in (
            ("run", first),
            ("show", shown),
            ("approve", approved),
            ("run again", second),
            ("reject", rejected),
        )
        for stream, text in (("stdout", result.stdout), ("stderr", result.stderr))
    ]
    searched.extend(every_file(approved_artifacts))
    searched.extend(every_file(rejected_artifacts))
    names = [name for name, _ in searched]
    assert any(name.endswith("audit.jsonl") for name in names)
    assert any(name.endswith("workflow.json") for name in names)
    assert any("trace.segment2.zip!" in name for name in names)
    assert [
        (name, leaks(data, LEAK_SECRET)) for name, data in searched if leaks(data, LEAK_SECRET)
    ] == []
