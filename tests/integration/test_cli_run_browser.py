"""``mendwork run`` end to end: its own Chromium, its real output channels, and its exit codes.

Each test launches Chromium through the CLI, which is what makes the module slow. The
secret leakage test runs against a JS-free fixture site: the chaos portal ships its demo
password in its own JavaScript, so a scan of anything the portal served would always find
it and prove nothing.
"""

import base64
import json
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Final
from urllib.parse import quote_plus

import pytest
from typer.testing import CliRunner, Result

from benchmarks.chaos.local_egress import local_policy
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.main import app
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.domain.documents import parse_workflow_document
from tests.integration.portal import DEMO_EMAIL, DEMO_PASSWORD
from tests.secret_search import every_file, leaks
from tests.workflows import REPO_ROOT, Document, example_path

pytestmark = [pytest.mark.browser, pytest.mark.slow]

Invoke = Callable[[list[str], dict[str, str]], Result]
FIXTURE_SITE: Final = REPO_ROOT / "tests" / "fixtures" / "sites" / "secret_login"
LEAK_SECRET: Final = "Zq7-leak/probe &4421 ü"


@pytest.fixture
def cli_browser(tmp_path: Path) -> Invoke:
    """Run ``mendwork run`` in-process; it launches its own Chromium each time.

    The local servers the run's inputs name are the only loopback origins it may reach, and the
    test's own workflow store is the only one it uses unless the arguments name another.
    """

    def invoke(arguments: list[str], env: dict[str, str]) -> Result:
        values = [argument.split("=", 1)[1] for argument in arguments if "=" in argument]
        origins = [exception.origin for exception in local_policy(values).loopback_exceptions]
        egress = {"MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS": json.dumps(origins)}
        store = (
            [] if "--store-dir" in arguments else ["--store-dir", str(tmp_path / "workflow-store")]
        )
        return CliRunner().invoke(app, ["run", *arguments, *store], env={**egress, **env})

    return invoke


def portal_arguments(portal_url: str, artifacts: Path) -> list[str]:
    return [
        "--artifacts-dir",
        str(artifacts),
        "--input",
        f"portal_url={portal_url}index.html",
        "--input",
        f"account_email={DEMO_EMAIL}",
    ]


def test_a_level_zero_run_succeeds_with_readable_progress_and_summary(
    cli_browser: Invoke, portal_url: str, tmp_path: Path, plain_stdout: Callable[[Result], str]
) -> None:
    result = cli_browser(
        [str(example_path("download_report")), *portal_arguments(portal_url, tmp_path)],
        {"MENDWORK_SECRET_PORTAL_PASSWORD": DEMO_PASSWORD},
    )

    output = plain_stdout(result)
    assert result.exit_code == 0, result.stderr
    assert "[9/9] download_csv · click" in output
    assert "passed download_completed shipments_2026-02-10_to_2026-04-20.csv" in output
    assert "SUCCEEDED · 9/9 steps in " in output
    assert "Download: " in output
    assert "run_started" not in output
    assert "run_started" in result.stderr


def test_a_failed_checkpoint_exits_1_with_json_events_and_evidence(
    cli_browser: Invoke, portal_url: str, tmp_path: Path
) -> None:
    source = example_path("download_report").read_text(encoding="utf-8")
    copy = tmp_path / "download_report_wrong_date.yaml"
    copy.write_text(source.replace('value: "2026-04-20"', 'value: "2026-04-21"'), encoding="utf-8")

    result = cli_browser(
        [str(copy), "--output", "json", *portal_arguments(portal_url, tmp_path / "artifacts")],
        {
            "MENDWORK_SECRET_PORTAL_PASSWORD": DEMO_PASSWORD,
            "MENDWORK_CHECKPOINT_TIMEOUT_MS": "1500",
        },
    )

    assert result.exit_code == 1, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    events, final = lines[:-1], lines[-1]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert (events[0]["type"], events[-1]["type"]) == ("run_started", "run_finished")
    assert [event["type"] for event in events if event["type"] == "checkpoint_failed"] == [
        "checkpoint_failed"
    ]
    assert (final["result_version"], final["exit_code"]) == (1, 1)
    failed = final["run"]["steps"][7]
    assert (failed["step_id"], failed["error"]["type"]) == ("apply_filter", "CheckpointFailed")
    run_directory = tmp_path / "artifacts" / "runs" / final["run"]["run_id"]
    for name in (
        failed["artifacts"]["screenshot"],
        failed["artifacts"]["dom_snapshot"],
        failed["artifacts"]["trace"],
    ):
        assert (run_directory / name).is_file(), name


# Secret leakage.


def fixture_workflow(failing_step: str) -> Document:
    """Sign in to the fixture site; fail after the password page is gone, or while it is up."""
    email = {
        "id": "fill_email",
        "intent": "Fill the 'Email address' field",
        "action": "fill",
        "risk": "caution",
        "target": {
            "tag": "input",
            "role": "textbox",
            "accessible_name": "Email address",
            "attributes": {"type": "email"},
            "structural_path": "main > form > input",
            "selectors": [{"strategy": "test_id", "value": "email"}],
        },
        "value": {"kind": "input", "name": "account_email"},
        "checkpoints": [{"kind": "field_has_value"}],
    }
    password_checks: list[dict[str, object]] = [{"kind": "field_has_value"}]
    if failing_step == "fill_password":
        password_checks.append(
            {
                "kind": "element_visible",
                "selector": {"strategy": "css", "value": "#never"},
                "timeout_ms": 400,
            }
        )
    password = {
        "id": "fill_password",
        "intent": "Fill the 'Password' field",
        "action": "fill",
        "risk": "caution",
        "target": {
            "tag": "input",
            "accessible_name": "Password",
            "attributes": {"type": "password"},
            "structural_path": "main > form > input",
            "selectors": [{"strategy": "test_id", "value": "password"}],
        },
        "value": {"kind": "secret", "name": "site_password"},
        "checkpoints": password_checks,
    }
    sign_in = {
        "id": "sign_in",
        "intent": "Click the 'Sign in' button",
        "action": "click",
        "risk": "caution",
        "target": {
            "tag": "button",
            "role": "button",
            "accessible_name": "Sign in",
            "attributes": {"type": "submit"},
            "structural_path": "main > form > button",
            "selectors": [{"strategy": "test_id", "value": "sign-in"}],
        },
        "checkpoints": [
            {
                "kind": "url_matches",
                "mode": "regex",
                "pattern": r"https?://[^?#]+/app\.html(?:[?#].*)?",
            }
        ],
    }
    export = {
        "id": "export",
        "intent": "Click the 'Export' button",
        "action": "click",
        "risk": "safe",
        "target": {
            "tag": "button",
            "role": "button",
            "accessible_name": "Export",
            "attributes": {"type": "button"},
            "structural_path": "main > button",
            "selectors": [{"strategy": "test_id", "value": "export"}],
        },
        "checkpoints": [{"kind": "text_present", "text": "Export complete", "timeout_ms": 400}],
    }
    opening = {
        "id": "open",
        "intent": "Open the sign-in page",
        "action": "navigate",
        "risk": "safe",
        "value": {"kind": "input", "name": "site_url"},
    }
    steps = (
        [opening, email, password]
        if failing_step == "fill_password"
        else [opening, email, password, sign_in, export]
    )
    return {
        "schema_version": 1,
        "workflow_id": "leak_probe",
        "version": 1,
        "created_at": "2026-09-11T00:00:00Z",
        "inputs": [{"name": "site_url", "kind": "url"}, {"name": "account_email", "kind": "text"}],
        "secrets": ["site_password"],
        "steps": steps,
    }


@pytest.fixture(scope="module")
def fixture_site() -> Iterator[str]:
    with PortalServer(FIXTURE_SITE, host="127.0.0.1", port=0) as server:
        yield server.url


def test_the_leak_search_would_find_a_planted_secret(tmp_path: Path) -> None:
    planted = tmp_path / "planted.zip"
    with zipfile.ZipFile(planted, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "resources/blob", b"prefix" + base64.b64encode(b"ab" + LEAK_SECRET.encode())
        )

    assert any(leaks(data, LEAK_SECRET) for _, data in every_file(tmp_path))
    assert leaks(quote_plus(LEAK_SECRET).encode(), LEAK_SECRET)


@pytest.mark.parametrize("failing_step", ["export", "fill_password"])
def test_secret_values_never_leave_the_run(
    cli_browser: Invoke, fixture_site: str, tmp_path: Path, failing_step: str
) -> None:
    workflow = tmp_path / "leak_probe.yaml"
    workflow.write_bytes(
        WorkflowYamlCodec(max_bytes=1 << 20).encode(
            parse_workflow_document(fixture_workflow(failing_step))
        )
    )
    artifacts = tmp_path / "artifacts"

    result = cli_browser(
        [
            str(workflow),
            "--output",
            "json",
            "--artifacts-dir",
            str(artifacts),
            "--input",
            f"site_url={fixture_site}index.html",
            "--input",
            "account_email=ada@example.test",
        ],
        {"MENDWORK_SECRET_SITE_PASSWORD": LEAK_SECRET, "MENDWORK_LOG_LEVEL": "DEBUG"},
    )

    assert result.exit_code == 1, result.stderr
    final = json.loads(result.stdout.splitlines()[-1])
    steps = {step["step_id"]: step for step in final["run"]["steps"]}
    assert steps["fill_password"]["checkpoints"][0] == {
        "index": 0,
        "kind": "field_has_value",
        "passed": True,
        "reason": None,
        "detail": None,
    }
    failed = steps[failing_step]["artifacts"]
    run_directory = artifacts / "runs" / final["run"]["run_id"]
    if failing_step == "export":
        assert failed["trace"] == "failure/trace.zip"
        with zipfile.ZipFile(run_directory / "failure" / "trace.zip") as archive:
            assert b'"export"' in b"".join(archive.read(name) for name in archive.namelist())
    else:
        assert failed["trace"] is None
        assert failed["trace_withheld"]["reason"] == "secret_bearing_page"
        assert (run_directory / failed["dom_snapshot"]).is_file()

    searched = [
        ("stdout", result.stdout.encode()),
        ("stderr", result.stderr.encode()),
        *every_file(run_directory),
    ]
    assert len(searched) >= 6
    assert [
        (name, leaks(data, LEAK_SECRET)) for name, data in searched if leaks(data, LEAK_SECRET)
    ] == []
