"""What crosses from a recording page into Python: never a secret, and one page global.

The inbound transcript holds every payload the recording page scripts sent or returned. A
distinctive secret typed like a person types it, into a password field, a CSS-masked field
with a neutral label, and a field inside a masked container, must not appear in it in any
encoding. The plain email must appear, which proves the transcript sees value reads.
"""

import json
from collections.abc import AsyncIterator
from typing import Final

import pytest
import pytest_asyncio
from playwright.async_api import Browser

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.recording import LiteralDraft, SecretDraft
from tests.integration.recording_harness import RecordingOutcome, ScriptedUser, record_scripted
from tests.integration.recording_pages import BOUNDARY, ORIGIN, site
from tests.secret_search import leaks

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.asyncio(loop_scope="session")]

SECRET: Final = "Zq7-rec/probe &9913 ü"
EMAIL: Final = "ada@example.test"
GLOBALS: list[str] = []


async def person(user: ScriptedUser) -> None:
    page = user.page
    GLOBALS.extend(
        await page.evaluate(
            "() => Object.getOwnPropertyNames(window)"
            ".filter((name) => /mendwork|playwright/i.test(name)).sort()"
        )
    )
    await page.fill("#email", EMAIL)
    for field in ("#password", "#code", "#note"):
        await page.click(field)
        await page.keyboard.type(SECRET)
    await page.get_by_role("button", name="Continue").click()
    await user.steps(6)


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def outcome(browser: Browser) -> AsyncIterator[RecordingOutcome]:
    yield await record_scripted(browser, f"{ORIGIN}sign-in.html", person, prepare=site(BOUNDARY))


def transcript_bytes(outcome: RecordingOutcome) -> bytes:
    return json.dumps(outcome.transcript, ensure_ascii=False, default=str).encode()


async def test_the_secret_never_reached_python(outcome: RecordingOutcome) -> None:
    received = transcript_bytes(outcome)

    assert len(outcome.transcript) > 20
    assert {source for source, _ in outcome.transcript} >= {
        "binding",
        "element_facts",
        "field_text",
    }
    assert leaks(received, SECRET) == []
    assert EMAIL.encode() in received


async def test_masked_fields_are_recorded_as_secrets(outcome: RecordingOutcome) -> None:
    steps = outcome.recorded.steps

    assert steps[1].value == LiteralDraft(value=EMAIL, hint="email")
    assert [type(step.value) for step in steps[2:5]] == [SecretDraft, SecretDraft, SecretDraft]
    assert [step.value.reason for step in steps[2:5] if isinstance(step.value, SecretDraft)] == [
        'its type is "password"',
        "it is masked on the page",
        "it is masked on the page",
    ]


async def test_the_secret_is_in_nothing_the_recording_produced(outcome: RecordingOutcome) -> None:
    workflow = WorkflowYamlCodec(max_bytes=1 << 20).encode(outcome.workflow("sign_in"))
    notices = json.dumps([notice.model_dump(mode="json") for notice in outcome.notices]).encode()

    assert leaks(workflow, SECRET) == []
    assert leaks(notices, SECRET) == []


async def test_the_search_would_find_the_secret() -> None:
    assert leaks(json.dumps({"value": SECRET}).encode(), SECRET)


async def test_the_page_sees_one_mendwork_global(outcome: RecordingOutcome) -> None:
    assert outcome.recording is not None
    assert GLOBALS == [
        "__mendwork",
        "__playwright__binding__",
        "__playwright__binding__controller__",
    ]
