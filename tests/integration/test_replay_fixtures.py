"""Replay against in-memory pages: the cases the chaos portal does not cover.

Pages are served by Playwright routes on the run's own context, so nothing touches the
network. Adapter-level cases drive a session directly on `page.set_content` documents.
"""

import asyncio
import zipfile
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Final

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, Route
from pydantic import SecretStr, TypeAdapter

from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher, SessionOptions
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.engine.domain.documents import parse_workflow_document
from mendwork.engine.domain.runs import RunStatus, TraceWithheldReason, parse_run_id
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.ports.browser_types import SecretText, TraceNotSaved, TraceSaved
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.integration.replay_harness import PageHook, ReplayOutcome, replay, replay_settings
from tests.workflows import Document

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

ORIGIN: Final = "https://fixture.mendwork.test/"
SELECTOR: TypeAdapter[Selector] = TypeAdapter(Selector)
OPTIONS: Final = SessionOptions(
    viewport_width=1280, viewport_height=720, default_timeout_ms=5_000, trace_on_failure=True
)


def css(value: str) -> dict[str, str]:
    return {"strategy": "css", "value": value}


def click_workflow(
    page: str,
    selectors: list[dict[str, str]],
    checkpoints: list[Document] | None = None,
    name: str = "Save",
) -> Document:
    return {
        "schema_version": 1,
        "workflow_id": "fixture",
        "version": 1,
        "created_at": "2026-09-11T00:00:00Z",
        "steps": [
            {
                "id": "open",
                "intent": "Open the fixture page",
                "action": "navigate",
                "risk": "safe",
                "value": {"kind": "literal", "value": f"{ORIGIN}{page}"},
            },
            {
                "id": "save",
                "intent": f"Click the '{name}' button",
                "action": "click",
                "risk": "safe",
                "target": {
                    "tag": "button",
                    "role": "button",
                    "accessible_name": name,
                    "structural_path": "main > button",
                    "selectors": selectors,
                },
                "checkpoints": checkpoints or [],
            },
        ],
    }


def serve(pages: Mapping[str, str]) -> PageHook:
    """A hook that serves in-memory pages under ORIGIN on the run's own browser context."""

    async def prepare(page: Page) -> None:
        async def fulfil(route: Route) -> None:
            path = route.request.url.removeprefix(ORIGIN)
            if path in pages:
                content_type = "application/json" if path.startswith("api/") else "text/html"
                await route.fulfill(
                    status=200,
                    content_type=content_type,
                    body=pages[path],
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            else:
                await route.fulfill(status=404, body="not found")

        await page.context.route(f"{ORIGIN}**", fulfil)

    return prepare


async def run_fixture(
    browser: Browser,
    tmp_path: Path,
    document: Document,
    pages: Mapping[str, str],
    **timing: int,
) -> ReplayOutcome:
    return await replay(
        browser,
        parse_workflow_document(document),
        {},
        tmp_path,
        settings=replay_settings(**timing),
        prepare=serve(pages),
    )


async def test_selectors_that_find_different_elements_abstain_without_clicking(
    browser: Browser, tmp_path: Path
) -> None:
    page = """<main>
      <button data-testid="save" onclick="document.title='clicked save'">Save</button>
      <button id="other" onclick="document.title='clicked other'">Save</button>
    </main>"""
    titles: list[str] = []

    async def inspect(page_: Page) -> None:
        titles.append(await page_.title())

    outcome = await replay(
        browser,
        parse_workflow_document(
            click_workflow("page.html", [{"strategy": "test_id", "value": "save"}, css("#other")])
        ),
        {},
        tmp_path,
        prepare=serve({"page.html": page}),
        inspect=inspect,
    )

    step = outcome.step("save")
    assert step.error is not None
    assert step.error.type == "HealAbstained"
    assert step.heal is not None
    rung0 = step.heal.attempts[0]
    assert (rung0.rung, rung0.outcome) == (0, "ambiguous")
    assert rung0.target is not None
    assert [identity.name for identity in rung0.target.elements] == ["Save", "Save"]
    assert not step.action_performed
    assert titles == [""]


async def test_a_control_that_renders_late_is_waited_for(browser: Browser, tmp_path: Path) -> None:
    page = """<main id="main"></main><script>
      setTimeout(() => {
        const button = document.createElement("button");
        button.id = "save";
        button.textContent = "Save";
        document.getElementById("main").append(button);
      }, 300);
    </script>"""

    outcome = await run_fixture(
        browser, tmp_path, click_workflow("page.html", [css("#save")]), {"page.html": page}
    )

    assert outcome.run.status is RunStatus.SUCCEEDED


async def test_a_page_that_never_stops_changing_is_never_acted_on(
    browser: Browser, tmp_path: Path
) -> None:
    # A message loop mutates the DOM between every task the page runs, so a mutation lands
    # between any two of Rung 0's round trips. (A page that mutates once per animation frame
    # can still be read consistently between frames, and is then correctly resolved.)
    page = """<main><button id="save">Save</button><span id="tick"></span></main><script>
      const channel = new MessageChannel();
      let n = 0;
      channel.port1.onmessage = () => {
        document.getElementById("tick").setAttribute("data-n", String(n++));
        channel.port2.postMessage(0);
      };
      channel.port2.postMessage(0);
    </script>"""

    outcome = await run_fixture(
        browser,
        tmp_path,
        click_workflow("page.html", [css("#save")]),
        {"page.html": page},
        step_timeout_ms=1_500,
        settle_timeout_ms=200,
    )

    error = outcome.step("save").error
    assert error is not None
    assert error.type == "PageNeverStable"
    assert error.message == "the page kept changing, so the target could not be verified safely"
    assert not outcome.step("save").action_performed


async def test_a_click_that_opens_a_new_tab_fails_clearly(browser: Browser, tmp_path: Path) -> None:
    # window.open, not a target=_blank link: headless Chromium opens no tab for such a link
    # on a routed page, which would make this test pass for the wrong reason.
    page = f"""<main><button onclick="window.open('{ORIGIN}other.html')">Save</button></main>"""
    document = click_workflow("page.html", [css("button")])

    outcome = await run_fixture(
        browser, tmp_path, document, {"page.html": page, "other.html": "<p>other</p>"}
    )

    error = outcome.step("save").error
    assert error is not None
    assert (error.type, error.context["reason"]) == ("NavigationError", "new_page_opened")


async def test_a_response_caused_by_the_click_is_observed(browser: Browser, tmp_path: Path) -> None:
    page = f"""<main><button id="save" onclick="fetch('{ORIGIN}api/save')">Save</button></main>"""
    checkpoint = {
        "kind": "response_received",
        "mode": "exact",
        "pattern": f"{ORIGIN}api/save",
        "status_min": 200,
        "status_max": 299,
    }

    outcome = await run_fixture(
        browser,
        tmp_path,
        click_workflow("page.html", [css("#save")], [checkpoint]),
        {"page.html": page, "api/save": "{}"},
    )

    assert outcome.run.status is RunStatus.SUCCEEDED
    assert outcome.step("save").checkpoints[0].detail == f"200 {ORIGIN}api/save"


@pytest_asyncio.fixture(loop_scope="session")
async def session(browser: Browser) -> AsyncIterator[PlaywrightSession]:
    launcher = await PlaywrightLauncher.create(browser, OPTIONS)
    async with launcher.session(parse_run_id("20260911T000000Z-00000001")) as opened:
        yield opened


async def test_an_element_replaced_after_verification_is_never_clicked(
    session: PlaywrightSession,
) -> None:
    await session.page.set_content(
        """<button id="go" onclick="window.clicks = (window.clicks || 0) + 1">Go</button>"""
    )
    match = await session.resolve_unique(SELECTOR.validate_python(css("#go")))
    assert match.element is not None
    await session.page.evaluate("""() => {
      const fresh = document.createElement("button");
      fresh.id = "go";
      fresh.textContent = "Go";
      fresh.onclick = () => { window.clicks = (window.clicks || 0) + 1; };
      document.getElementById("go").replaceWith(fresh);
    }""")

    with pytest.raises(TargetNotFound) as caught:
        await session.click(match.element, timeout_ms=1_000)

    assert caught.value.context["reason"] == "detached_before_action"
    assert await session.page.evaluate("() => window.clicks ?? 0") == 0


async def test_hidden_duplicates_do_not_make_a_visible_control_ambiguous(
    session: PlaywrightSession,
) -> None:
    await session.page.set_content(
        "<button hidden>Save</button>"
        "<div style='display:none'><button>Save</button></div>"
        "<button>Save</button>"
    )

    match = await session.resolve_unique(
        SELECTOR.validate_python({"strategy": "role_name", "role": "button", "name": "Save"})
    )

    assert (match.level_counts, match.element is not None) == ((1,), True)


async def test_a_trace_is_withheld_while_a_typed_secret_is_still_on_the_page(
    session: PlaywrightSession,
) -> None:
    secret = "trace-probe-5519-secret"
    scrubber = SecretScrubber()
    scrubber.register(SecretStr(secret))
    await session.page.context.route(
        f"{ORIGIN}**",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<button>Next</button>"
        ),
    )
    await session.navigate(f"{ORIGIN}login.html", timeout_ms=5_000)
    await session.page.set_content("<input id='key' type='password'>")
    field = await session.resolve_unique(SELECTOR.validate_python(css("#key")))
    assert field.element is not None

    await session.fill(field.element, SecretText(value=SecretStr(secret)), timeout_ms=2_000)
    withheld = await session.export_trace(scrubber=scrubber)

    assert withheld == TraceNotSaved(reason=TraceWithheldReason.SECRET_BEARING_PAGE)


async def test_a_trace_recorded_after_the_secret_page_is_gone_is_kept_and_clean(
    browser: Browser,
) -> None:
    secret = "trace-probe-7731-secret"
    scrubber = SecretScrubber()
    scrubber.register(SecretStr(secret))
    launcher = await PlaywrightLauncher.create(browser, OPTIONS)
    async with launcher.session(parse_run_id("20260911T000000Z-00000002")) as live:
        await live.page.context.route(
            f"{ORIGIN}**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<input id='key' type='password'><button id='next'>Next</button>",
            ),
        )
        await live.navigate(f"{ORIGIN}login.html", timeout_ms=5_000)
        field = await live.resolve_unique(SELECTOR.validate_python(css("#key")))
        assert field.element is not None
        await live.fill(field.element, SecretText(value=SecretStr(secret)), timeout_ms=2_000)
        await live.navigate(f"{ORIGIN}home.html", timeout_ms=5_000)
        button = await live.resolve_unique(SELECTOR.validate_python(css("#next")))
        assert button.element is not None
        await live.click(button.element, timeout_ms=2_000)

        export = await live.export_trace(scrubber=scrubber)

        assert isinstance(export, TraceSaved)
        data = await asyncio.to_thread(_all_members, export.path)
        assert b"click" in data
        assert secret.encode() not in data


def _all_members(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return b"".join(archive.read(name) for name in archive.namelist())


async def test_fields_filled_from_secrets_are_masked_in_screenshots(
    session: PlaywrightSession,
) -> None:
    await session.page.set_content(
        "<main style='padding:40px'><input id='key' type='text' style='font-size:32px'></main>"
    )
    key = SELECTOR.validate_python(css("#key"))
    field = await session.resolve_unique(key)
    assert field.element is not None

    shots: list[bytes] = []
    unmasked: list[bytes] = []
    for value in ("first-secret-AAAA", "other-secret-ZZZZ"):
        await session.fill(field.element, SecretText(value=SecretStr(value)), timeout_ms=2_000)
        shots.append(await session.screenshot(mask=[key], timeout_ms=5_000))
        unmasked.append(await session.screenshot(mask=[], timeout_ms=5_000))

    assert shots[0] == shots[1]
    assert unmasked[0] != unmasked[1]


IDENTITY_CORPUS: Final = """
<main>
  <h2 id="title">Shipment reports</h2>
  <button id="text">Download   CSV</button>
  <button id="icon" aria-label="Download CSV">
    <svg aria-hidden="true" viewBox="0 0 1 1"><path d="M0 0"/></svg>
  </button>
  <a id="link" href="/reports">View <b>reports</b></a>
  <label for="email">Email address</label><input id="email" type="email">
  <label for="password">Password</label><input id="password" type="password">
  <label>From <input id="from" type="date"></label>
  <label for="status">Status</label><select id="status"><option>Open</option></select>
  <span id="caption">Order number</span><input id="order" aria-labelledby="caption">
  <input id="search" type="search" placeholder="Search orders">
  <input id="submit" type="submit" value="Apply filter">
  <input id="agree" type="checkbox"><label for="agree">I agree</label>
  <div id="tab" role="tab">Details <span hidden>secret tab text</span></div>
  <style>#pseudo::before { content: "Next "; }</style><button id="pseudo">page</button>
  <div id="card" role="button" tabindex="0">Open <input aria-label="count"></div>
</main>
"""


@pytest.mark.parametrize(
    ("css_selector", "role", "name", "confirmed"),
    [
        ("#title", "heading", "Shipment reports", True),
        ("#text", "button", "Download CSV", True),
        ("#icon", "button", "Download CSV", True),
        ("#link", "link", "View reports", True),
        ("#email", "textbox", "Email address", True),
        ("#password", "textbox", "Password", True),
        ("#from", "textbox", "From", True),
        ("#status", "combobox", "Status", True),
        ("#order", "textbox", "Order number", True),
        ("#search", "searchbox", "Search orders", True),
        ("#submit", "button", "Apply filter", True),
        ("#agree", "checkbox", "I agree", True),
        ("#tab", "tab", "Details", True),
        ("#pseudo", "button", "Next page", True),
        ("#card", "button", "Open", False),
    ],
)
async def test_computed_identity_agrees_with_playwrights_role_locator(
    session: PlaywrightSession, css_selector: str, role: str, name: str, confirmed: bool
) -> None:
    await session.page.set_content(IDENTITY_CORPUS)
    match = await session.resolve_unique(SELECTOR.validate_python(css(css_selector)))
    assert match.element is not None

    identity = await session.identify(match.element, confirm=True)

    assert (identity.role, identity.name, identity.confirmed) == (role, name, confirmed)
