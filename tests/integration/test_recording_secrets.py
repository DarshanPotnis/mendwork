"""A password typed while recording never leaves the page: not in the file, the output, the
logs, the notices, the transcript, or the verification replay's artifacts.

Slow: it records and then replays. It runs against the JS-free fixture site, because the
chaos portal ships its demo password in its own JavaScript, so a scan of anything the
portal served could never be clean.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from playwright.async_api import Browser

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.record_output import render_recording_summary
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.domain.runs import RunStatus
from tests.integration.recording_harness import ScriptedUser, record_scripted
from tests.integration.replay_harness import replay
from tests.secret_search import every_file, leaks
from tests.workflows import REPO_ROOT

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

FIXTURE_SITE: Final = REPO_ROOT / "tests" / "fixtures" / "sites" / "secret_login"
SECRET: Final = "Zq7-record/leak &5520 ü"


@pytest.fixture(scope="module")
def fixture_site() -> Iterator[str]:
    with PortalServer(FIXTURE_SITE, host="127.0.0.1", port=0) as server:
        yield server.url


async def person(user: ScriptedUser) -> None:
    page = user.page
    await page.get_by_test_id("email").fill("ada@example.test")
    await page.get_by_test_id("password").click()
    await page.keyboard.type(SECRET)
    await user.steps(2)
    await page.get_by_role("button", name="Sign in").click()
    await user.steps(4)
    await page.get_by_role("button", name="Export").click()
    await user.steps(5)


async def test_a_recorded_secret_never_appears_anywhere(
    browser: Browser, fixture_site: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded = await record_scripted(browser, f"{fixture_site}index.html", person)
    decisions = recorded.decisions()
    version = recorded.workflow("sign_in", decisions)
    workflow_file = tmp_path / "sign_in.yaml"
    workflow_file.write_bytes(WorkflowYamlCodec(max_bytes=1 << 20).encode(version))
    summary = render_recording_summary(recorded.recorded, decisions)

    replayed = await replay(
        browser,
        version,
        {decision.name: decision.value for decision in decisions.inputs},
        tmp_path / "verify",
        secrets={decision.name: SECRET for decision in decisions.secrets},
    )

    assert replayed.run.status is RunStatus.SUCCEEDED, replayed.run.error
    assert [decision.name for decision in decisions.secrets] == ["password"]
    captured = capsys.readouterr()
    searched = [
        ("workflow", workflow_file.read_bytes()),
        ("summary", summary.encode()),
        ("stdout", captured.out.encode()),
        ("stderr", captured.err.encode()),
        (
            "notices",
            json.dumps([notice.model_dump(mode="json") for notice in recorded.notices]).encode(),
        ),
        ("transcript", json.dumps(recorded.transcript, default=str, ensure_ascii=False).encode()),
        (
            "events",
            json.dumps([event.model_dump(mode="json") for event in replayed.events]).encode(),
        ),
        *every_file(tmp_path / "verify"),
    ]
    assert len(searched) >= 8
    assert [name for name, data in searched if leaks(data, SECRET)] == []
    assert b"ada@example.test" in searched[5][1]
