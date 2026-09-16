"""The script baselines in Chromium: one locator, Playwright's strictness, the workflow's checks.

Pages are served under a made-up host on the run's own context, each with a small stand-in for the
chaos portal's ground-truth API, so the runner's checks can be exercised on exactly the case each
test names (ADR 0014).
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Final

import pytest
from playwright.async_api import Browser, Route
from pydantic import JsonValue

from benchmarks.baselines.script_runner import ScriptRun, ScriptRunner
from benchmarks.chaos.systems import ScriptKind
from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.browser_playwright.session import PlaywrightSession
from mendwork.apps.cli.wiring import egress_enforcement, session_options
from mendwork.engine.benchmark.cells import UNRECORDED
from mendwork.engine.benchmark.truth import StopKind, TargetMatch, Verdict
from mendwork.engine.domain.documents import parse_workflow_document
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.runs import RunId
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.settings import Settings
from tests.fakes.egress import FakeResolver

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]

Json = dict[str, JsonValue]
HOST: Final = "script.mendwork.test"
ORIGIN: Final = f"https://{HOST}/"
SECRET: Final = "fixture_password"
UNREACHABLE: Final = "unreachable.html"
TARGETS: Final = {
    "fill_email": "fixture.email",
    "fill_password": "fixture.password",
    "export": "fixture.export",
    "save": "fixture.save",
}
PAGE: Final = """<!doctype html>
<html lang="en"><head><title>Fixture</title></head><body><main>
<h1>Fixture</h1>
<label for="email">Email</label><input id="email" type="email">
<label for="password">Password</label><input id="password" type="password">
<button id="save" type="button">Save draft</button>
<p id="status" role="status"></p>
{extra}
</main>
<script>
const ids = {{"fixture.email": "email", "fixture.password": "password", "fixture.save": "save",
  "fixture.export": "{export_id}"}};
window.__chaos = {{
  pageId: "fixture",
  wrongActions: [],
  locate: (key) => document.getElementById(ids[key]),
}};
document.getElementById("save").addEventListener("click", () => {{
  document.getElementById("status").textContent = "Draft saved";
}});
for (const decoy of document.querySelectorAll("[data-decoy]")) {{
  decoy.addEventListener("click", () => {{
    window.__chaos.wrongActions.push({{ label: decoy.textContent }});
  }});
}}
</script>
</body></html>"""


def page(extra: str = "", export_id: str = "export-gone") -> str:
    return PAGE.format(extra=extra, export_id=export_id)


def workflow(*steps: Json) -> WorkflowVersion:
    uses_secret = any(
        isinstance(step.get("value"), dict) and step["value"] == {"kind": "secret", "name": SECRET}
        for step in steps
    )
    navigate: Json = {
        "id": "open",
        "intent": "Open the fixture",
        "action": "navigate",
        "risk": "safe",
        "value": {"kind": "input", "name": "page_url"},
    }
    return parse_workflow_document(
        {
            "schema_version": 1,
            "workflow_id": "script_fixture",
            "version": 1,
            "created_at": "2026-09-15T00:00:00Z",
            "inputs": [{"name": "page_url", "kind": "url"}],
            "secrets": [SECRET] if uses_secret else [],
            "steps": [navigate, *steps],
        }
    )


def click(step_id: str, name: str, selectors: list[JsonValue], text: str) -> Json:
    return {
        "id": step_id,
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
        "checkpoints": [{"kind": "text_present", "text": text, "timeout_ms": 800}],
    }


def fill(step_id: str, name: str, element_id: str, value: Json) -> Json:
    return {
        "id": step_id,
        "intent": f"Fill the '{name}' field",
        "action": "fill",
        "risk": "caution",
        "target": {
            "tag": "input",
            "accessible_name": name,
            "label_text": name,
            "attributes": {"id": element_id, "type": element_id},
            "structural_path": "main > input",
            "selectors": [
                {"strategy": "label", "value": name},
                {"strategy": "css", "value": f"#{element_id}"},
            ],
        },
        "value": value,
        "checkpoints": [{"kind": "field_has_value"}],
    }


EXPORT: Final = click(
    "export",
    "Export data",
    [
        {"strategy": "role_name", "role": "button", "name": "Export data"},
        {"strategy": "css", "value": "#export"},
    ],
    "Exported",
)
SAVE: Final = click(
    "save",
    "Save draft",
    [
        {"strategy": "role_name", "role": "button", "name": "Save draft"},
        {"strategy": "css", "value": "#save"},
    ],
    "Draft saved",
)


class ServedLauncher:
    """The product's launcher, with the fixture pages routed on each session's context."""

    def __init__(self, inner: PlaywrightLauncher, pages: Mapping[str, str]) -> None:
        self._inner = inner
        self._pages = pages

    @asynccontextmanager
    async def session(
        self, run_id: RunId, egress: EgressPolicy
    ) -> AsyncIterator[PlaywrightSession]:
        async with self._inner.session(run_id, egress) as session:
            await session.page.context.route(f"{ORIGIN}**", self._fulfil)
            yield session

    async def _fulfil(self, route: Route) -> None:
        path = route.request.url.removeprefix(ORIGIN)
        if path == UNREACHABLE:
            # A page that cannot be fetched at all, so page.goto itself raises.
            await route.abort("connectionrefused")
            return
        body = self._pages.get(path)
        if body is None:
            await route.fulfill(status=404, body="not found")
        else:
            await route.fulfill(status=200, content_type="text/html", body=body)


async def run_script(
    browser: Browser,
    kind: ScriptKind,
    flow: WorkflowVersion,
    html: str,
    *,
    start_url: str | None = None,
) -> ScriptRun:
    settings = Settings(_env_file=None).model_copy(
        update={"trace_on_failure": False, "step_timeout_ms": 800, "checkpoint_timeout_ms": 800}
    )
    inner = await PlaywrightLauncher.create(
        browser, session_options(settings), egress_enforcement(settings, FakeResolver())
    )
    runner = ScriptRunner(
        ServedLauncher(inner, {"index.html": html}),
        settings,
        kind=kind,
        targets=TARGETS,
        inputs={"page_url": start_url or f"{ORIGIN}index.html"},
        secrets={SECRET: "correct horse"},
        egress=EgressPolicy(allowed_domains=(HOST,)),
        signed_in=False,
    )
    return await runner.run(flow)


async def test_a_css_script_acts_on_a_control_that_kept_its_id_but_changed_its_meaning(
    browser: Browser,
) -> None:
    html = page('<button id="export" type="button" data-decoy>Delete data</button>')

    run = await run_script(browser, "css_selector", workflow(EXPORT, SAVE), html)

    export, save = run.observations
    assert run.status == "failed"
    assert export.stop is StopKind.CHECKPOINT_FAILED
    assert [(action.on_target, action.verdict) for action in export.actions] == [
        (TargetMatch.OFF_TARGET, Verdict.FAILED)
    ]
    assert export.page_wrong_actions == 1
    assert save.stop is StopKind.NOT_REACHED


async def test_a_role_script_finds_nothing_by_the_old_name_and_declines(browser: Browser) -> None:
    html = page('<button id="export" type="button" data-decoy>Delete data</button>')

    run = await run_script(browser, "role_name", workflow(EXPORT, SAVE), html)

    export, save = run.observations
    assert (export.stop, export.stop_reason, export.actions) == (
        StopKind.DECLINED,
        "locator_timeout",
        (),
    )
    assert export.target_available_at_stop is False
    assert save.stop is StopKind.NOT_REACHED


async def test_playwright_strict_mode_refuses_two_matching_controls(browser: Browser) -> None:
    duplicates = (
        '<button id="export-one" type="button" data-decoy>Export data</button>'
        '<button id="export-two" type="button" data-decoy>Export data</button>'
    )
    html = page(duplicates, export_id="export-one")

    run = await run_script(browser, "role_name", workflow(EXPORT), html)

    [export] = run.observations
    assert (export.stop, export.stop_reason, export.actions) == (
        StopKind.DECLINED,
        "strict_mode_violation",
        (),
    )
    assert export.target_available_at_stop is True


async def test_a_script_fills_and_clicks_the_real_controls_and_its_checks_pass(
    browser: Browser,
) -> None:
    steps = (
        fill("fill_email", "Email", "email", {"kind": "literal", "value": "a@b.test"}),
        fill("fill_password", "Password", "password", {"kind": "secret", "name": SECRET}),
        SAVE,
    )
    kinds: tuple[ScriptKind, ...] = ("css_selector", "role_name")

    for kind in kinds:
        run = await run_script(browser, kind, workflow(*steps), page())

        assert run.status == "succeeded", kind
        assert [item.stop for item in run.observations] == [StopKind.COMPLETED] * 3
        for item in run.observations:
            assert [(action.on_target, action.verdict) for action in item.actions] == [
                (TargetMatch.ON_TARGET, Verdict.PASSED)
            ], (kind, item.step_id)
            assert item.page_wrong_actions == 0


async def test_a_step_without_a_locator_of_the_scripts_kind_is_unexpressible(
    browser: Browser,
) -> None:
    css_only = click("save", "Save draft", [{"strategy": "css", "value": "#save"}], "Draft saved")

    run = await run_script(browser, "role_name", workflow(css_only), page())

    [save] = run.observations
    assert (save.stop, save.stop_reason, save.actions) == (
        StopKind.UNEXPRESSIBLE,
        "no role_name locator",
        (),
    )


async def test_a_run_that_cannot_load_its_first_page_says_so(browser: Browser) -> None:
    """A navigate acts on no control, so only the run failure can explain a cell that died here."""
    run = await run_script(
        browser,
        "css_selector",
        workflow(EXPORT),
        page(),
        start_url=f"{ORIGIN}{UNREACHABLE}",
    )

    assert run.status == "failed"
    assert run.failure is not None
    assert run.failure.targeted is False
    assert run.failure.action is ActionType.NAVIGATE
    assert run.failure.reason != UNRECORDED
    assert [item.stop for item in run.observations] == [StopKind.NOT_REACHED]
