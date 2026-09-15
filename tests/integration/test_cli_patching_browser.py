"""``mendwork run`` of a file end to end, with a model that answers over HTTP (ADR 0013).

Level 3 seed 3 changes the dashboard's link to Reports so that Rung 2 declines it. A scripted
OpenAI-compatible server on loopback plays the model and counts every request it receives. The
first run of the committed example stores it as v1, heals at Rung 3 with one request, and saves v2;
a rerun of the same file runs v2 and sends no request. ``history`` and ``diff`` read the heal back,
``rollback`` restores v1 as v3, and a run after it heals again but saves nothing, because the
rollback undid exactly that heal.
"""

import json
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner, Result

from benchmarks.chaos.local_egress import local_policy
from mendwork.apps.cli.main import app
from tests.integration.portal import DEMO_EMAIL, DEMO_PASSWORD
from tests.workflows import example_path

pytestmark = [pytest.mark.browser, pytest.mark.slow]

Mendwork = Callable[[list[str]], Result]
FILE: Final = str(example_path("download_report"))
TARGET_LINE: Final = re.compile(r'^(\d+)\. kind: link · name: "Open reports" · ', re.MULTILINE)
"""How the prompt lists the real target at level 3 seed 3; test_patching_guarantee checks the same
choice against the portal's ground truth."""
COMPLETIONS: Final = "/v1/chat/completions"


@dataclass
class ModelServer:
    """A Chat Completions server that chooses the listed line for the real target, or null."""

    url: str = ""
    requests: list[str] = field(default_factory=list)


def handler_for(server: ModelServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return None

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            server.requests.append(self.path)
            found = TARGET_LINE.search(str(request["messages"][-1]["content"]))
            answer = {
                "choice": int(found.group(1)) if found is not None else None,
                "confidence": 0.9,
                "reason": "The same control, renamed.",
            }
            reply = json.dumps(
                {
                    "choices": [
                        {"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 300, "completion_tokens": 30},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    return Handler


@pytest.fixture
def model_server() -> Iterator[ModelServer]:
    state = ModelServer()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(state))
    state.url = f"http://127.0.0.1:{httpd.server_port}/v1"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.fixture
def mendwork(portal_url: str, model_server: ModelServer, tmp_path: Path) -> Mendwork:
    """Run a command in-process with the scripted model, the test's own store and artifacts."""
    origins = [exception.origin for exception in local_policy([portal_url]).loopback_exceptions]
    env = {
        "MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS": json.dumps(origins),
        "MENDWORK_SECRET_PORTAL_PASSWORD": DEMO_PASSWORD,
        "MENDWORK_MODEL_PROVIDER": "openai_compatible",
        "MENDWORK_MODEL_NAME": "stub-model",
        "MENDWORK_MODEL_BASE_URL": model_server.url,
        "MENDWORK_MODEL_LOCAL": "true",
        "MENDWORK_WORKFLOW_STORE_DIR": str(tmp_path / "workflow-store"),
        "MENDWORK_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
    }

    def invoke(arguments: list[str]) -> Result:
        return CliRunner().invoke(app, arguments, env=env)

    return invoke


def test_a_file_s_model_heal_becomes_v2_its_rerun_asks_nothing_and_a_rollback_keeps_it_undone(
    mendwork: Mendwork,
    model_server: ModelServer,
    portal_url: str,
    tmp_path: Path,
    plain_stdout: Callable[[Result], str],
) -> None:
    store = tmp_path / "workflow-store"
    inputs = [
        "--input",
        f"portal_url={portal_url}index.html?seed=3&level=3",
        "--input",
        f"account_email={DEMO_EMAIL}",
    ]

    first = mendwork(["run", FILE, *inputs])
    after_first = len(model_server.requests)
    rerun = mendwork(["run", FILE, *inputs, "--output", "json"])
    after_rerun = len(model_server.requests)
    history = mendwork(["history", "download_report"])
    diff = mendwork(["diff", "download_report", "1"])
    rolled = mendwork(["rollback", "download_report", "--to", "1", "--reason", "check the choice"])
    after_rollback = mendwork(["run", FILE, *inputs, "--output", "json"])

    first_out = plain_stdout(first)
    assert first.exit_code == 0, first.stdout + first.stderr
    assert (
        f"Stored {FILE} as download_report v1 in {store}; heals from its runs are saved there."
        in first_out
    )
    assert "  Step 5 open_reports: saved as download_report v2 (rung 3, verified " in first_out
    report = re.search(r"Report: file://(\S+/runs/([^/]+)/report\.html)", first_out)
    assert report is not None
    assert "saved as download_report v2 (rung 3" in Path(report.group(1)).read_text("utf-8")
    first_run = report.group(2)
    assert (after_first, after_rerun) == (1, 1)

    assert rerun.exit_code == 0, rerun.stdout + rerun.stderr
    assert (
        f"Running download_report v2 from {store}: {FILE} is v1, and every later version came "
        "from a heal or a rollback."
    ) in rerun.stderr
    again = json.loads(rerun.stdout.splitlines()[-1])["run"]
    assert (again["status"], again["workflow_version"], again["patches"]) == ("succeeded", 2, [])
    assert again["model_usage"]["calls"] == 0
    assert [step["step_id"] for step in again["steps"] if step["heal"] is not None] == []

    assert history.exit_code == 0, history.stderr
    assert "Step 5 open_reports healed at rung 3, chosen by an AI model, verified " in plain_stdout(
        history
    )
    assert f"· run {first_run}" in plain_stdout(history)
    diff_out = plain_stdout(diff)
    assert diff.exit_code == 0, diff.stderr
    assert (
        "  Why: v2: Repaired by an AI model (rung 3), which chose it from the closest" in diff_out
    )
    assert 'Model: openai_compatible "stub-model", 1 call, 330 tokens, ' in diff_out

    assert rolled.exit_code == 0, rolled.stderr
    rolled_out = plain_stdout(rolled)
    assert "A heal it undoes is not saved again automatically" in rolled_out
    assert (
        "Rolled back download_report to v1 as v3. To restore v2: mendwork rollback "
        "download_report --to 2"
    ) in rolled_out

    assert after_rollback.exit_code == 0, after_rollback.stdout + after_rollback.stderr
    last = json.loads(after_rollback.stdout.splitlines()[-1])["run"]
    assert last["workflow_version"] == 3
    assert [(item["step_id"], item["result"], item["version"]) for item in last["patches"]] == [
        ("open_reports", "previously_rolled_back", 3)
    ]
    assert model_server.requests == [COMPLETIONS, COMPLETIONS]
    assert sorted(path.name for path in (store / "download_report").iterdir()) == [
        "v0001.yaml",
        "v0002.yaml",
        "v0003.yaml",
    ]
