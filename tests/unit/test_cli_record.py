"""``mendwork record`` in a terminal: usage, prompts, the summary, writing, and exit codes.

The browser and the verification replay are replaced through the command's dependencies, so
every path runs without Chromium; output is read through ``plain_stdout``.
"""

import asyncio
import signal
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import pytest
import typer
from typer.testing import CliRunner, Result

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.main import app as main_app
from mendwork.apps.cli.record import RecordDependencies, build_record_command, usage_problem
from mendwork.apps.cli.record_runtime import InterruptStop
from mendwork.engine.domain.runs import (
    ErrorCategory,
    ErrorReport,
    Run,
    RunStatus,
    parse_run_id,
)
from mendwork.engine.domain.steps import FillStep
from mendwork.engine.domain.values import InputValue, LiteralValue, SecretValue
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import BrowserUnavailable, MendworkError, SecretUnavailable
from mendwork.engine.ports.recording import RecordingLauncher
from mendwork.engine.ports.recording_types import ClickCapture, FieldValue, FillCapture
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings
from tests.fakes.browser import FakeBrowser, FakeElement
from tests.fakes.clock import FakeClock
from tests.fakes.ports import SequenceRandom
from tests.fakes.recording import FakeRecordingBrowser, FakeRecordingLauncher, NeverStop
from tests.unit.recording.builders import (
    DOC,
    START_URL,
    add_button,
    add_heading,
    browser,
    by_test_id,
    facts,
    ref,
    selector,
)

PlainStdout = Callable[[Result], str]
EMAIL = "ada@example.test"
CLOCK = FakeClock(datetime(2026, 9, 12, 10, 0, tzinfo=UTC))


class QuietStop(NeverStop):
    """A stop signal nobody presses, connected to nothing."""

    def connected(self) -> AbstractAsyncContextManager[None]:
        return self._connected()

    @asynccontextmanager
    async def _connected(self) -> AsyncIterator[None]:
        yield


def sign_in_page() -> FakeRecordingBrowser:
    page = browser(title="Sign in · Portal")
    add_heading(page, "Sign in")
    for key, element, label, kind, value in (
        ("email", 1, "Email address", "email", EMAIL),
        ("password", 2, "Password", "password", "hunter2"),
    ):
        page.elements[key] = FakeElement(
            tag="input", role="textbox", name=label, input_type=kind, value=value
        )
        page.facts[key] = facts(
            "input", data_testid=key, type=kind, label_text=label, text_entry=True
        )
        page.captured[(DOC, element)] = key
        page.finds[by_test_id(key)] = key
        page.finds[selector(strategy="label", value=label)] = key
        page.field_texts[key] = FieldValue(value=value)
    add_button(page, "save", name="Save", element=3)
    page.events.extend(
        [FillCapture(ref=ref(1, 1)), FillCapture(ref=ref(2, 2)), ClickCapture(ref=ref(3, 3))]
    )
    return page


def with_process_scrubber(ctx: typer.Context) -> None:
    """The entry point's part that commands rely on: the process's secret scrubber."""
    ctx.obj = SecretScrubber()


@dataclass
class Scenario:
    """What the fake browser and the fake verification replay do."""

    page: FakeRecordingBrowser = field(default_factory=sign_in_page)
    open_error: BaseException | None = None
    status: RunStatus = RunStatus.SUCCEEDED
    verify_error: MendworkError | None = None
    opened: int = 0
    verified: list[tuple[WorkflowVersion, dict[str, str]]] = field(default_factory=list)

    def dependencies(self) -> RecordDependencies:
        @asynccontextmanager
        async def open_recorder(
            settings: Settings, slow_mo_ms: int | None
        ) -> AsyncIterator[RecordingLauncher]:
            self.opened += 1
            if self.open_error is not None:
                raise self.open_error
            yield FakeRecordingLauncher(self.page)

        async def verify(
            version: WorkflowVersion,
            inputs: Mapping[str, str],
            settings: Settings,
            *,
            slow_mo_ms: int | None,
            stdout: TextIO,
            scrubber: SecretScrubber,
        ) -> Run:
            self.verified.append((version, dict(inputs)))
            if self.verify_error is not None:
                raise self.verify_error
            failed = self.status is RunStatus.FAILED
            return Run(
                run_id=parse_run_id("20260912T100000Z-00000001"),
                workflow_id=version.workflow_id,
                workflow_version=1,
                status=self.status,
                started_at=CLOCK.now(),
                error=ErrorReport(
                    type="CheckpointFailed", message="no", category=ErrorCategory.STEP
                )
                if failed
                else None,
            )

        return RecordDependencies(
            open_recorder=open_recorder,
            verify=verify,
            stop=QuietStop,
            clock=CLOCK,
            timer=lambda: self.page.timer,
            randomness=SequenceRandom,
        )

    def invoke(
        self, arguments: list[str], answers: str = "", env: dict[str, str] | None = None
    ) -> Result:
        app = typer.Typer()
        app.callback()(with_process_scrubber)
        app.command(name="record")(build_record_command(self.dependencies()))
        return CliRunner().invoke(app, ["record", *arguments], input=answers, env=env or {})


def written(path: Path) -> WorkflowVersion:
    return WorkflowYamlCodec(max_bytes=1 << 20).decode(path.read_bytes(), source=str(path))


def test_the_command_is_part_of_the_cli(plain_stdout: PlainStdout) -> None:
    result = CliRunner().invoke(main_app, ["record", "--help"])

    assert result.exit_code == 0
    assert "--no-verify" in plain_stdout(result)


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        (["ftp://portal.example.test/", "--out", "{dir}/demo.yaml"], "start URL must be"),
        ([START_URL, "--out", "{dir}/demo.json"], "--out must end in .yaml or .yml"),
        ([START_URL, "--out", "{dir}/existing.yaml"], "already exists"),
        ([START_URL, "--out", "{dir}/missing/demo.yaml"], "does not exist"),
        ([START_URL, "--out", "{dir}/Demo Workflow.yaml"], "becomes the workflow id"),
        (
            [START_URL, "--out", "{dir}/demo.yaml", "--input", "novalue"],
            "must look like name=value",
        ),
    ],
)
def test_bad_usage_exits_2_before_any_browser_opens(
    tmp_path: Path, arguments: list[str], problem: str
) -> None:
    (tmp_path / "existing.yaml").write_text("keep me\n", encoding="utf-8")
    scenario = Scenario()

    result = scenario.invoke([argument.format(dir=tmp_path) for argument in arguments])

    assert result.exit_code == 2
    assert problem in result.stderr
    assert scenario.opened == 0
    assert (tmp_path / "existing.yaml").read_text(encoding="utf-8") == "keep me\n"


def test_invalid_settings_exit_2(tmp_path: Path) -> None:
    result = Scenario().invoke(
        [START_URL, "--out", str(tmp_path / "demo.yaml")], env={"MENDWORK_STEP_TIMEOUT_MS": "0"}
    )

    assert result.exit_code == 2
    assert "invalid configuration" in result.stderr


def test_a_recording_is_summarized_named_written_and_verified(
    tmp_path: Path, plain_stdout: PlainStdout
) -> None:
    scenario = Scenario()
    out = tmp_path / "sign_in.yaml"

    result = scenario.invoke([START_URL, "--out", str(out)], answers="\n\n\n")

    output = plain_stdout(result)
    assert result.exit_code == 0, result.stderr
    assert f"recorded  1. NAVIGATE to {START_URL}" in output
    assert "Recorded 4 steps · 0 interactions ignored" in output
    assert " 2. FILL the 'Email address' field with a typed value" in output
    assert " 3. FILL the 'Password' field with a secret" in output
    assert " 4. CLICK the 'Save' button" in output
    # "Save" changes stored data, so the click is irreversible.
    assert "click_save · selector 1 of 3 (test_id) · risk irreversible" in output
    assert "secret name [password]:" in output
    assert "input [email_address: Email address typed into the 'Email address' field]: " in output
    assert "Wrote " in output
    assert "inputs: start_url, email_address · secrets: password" in output
    assert "VERIFIED" in output
    assert EMAIL not in output
    assert "hunter2" not in output + result.stderr
    version = written(out)
    assert version.workflow_id == "sign_in"
    assert scenario.verified[0][1] == {"start_url": START_URL, "email_address": EMAIL}
    assert b"hunter2" not in out.read_bytes()


def test_answers_override_names_keep_literals_and_are_validated(
    tmp_path: Path, plain_stdout: PlainStdout
) -> None:
    scenario = Scenario()
    out = tmp_path / "sign_in.yaml"

    result = scenario.invoke(
        [START_URL, "--out", str(out), "--input", f"portal_url={START_URL}"],
        answers="Portal Password\nportal_password\n-\n",
    )

    output = plain_stdout(result)
    assert result.exit_code == 0, result.stderr
    assert "must be a lowercase slug" in output
    email, password = (step for step in written(out).steps if isinstance(step, FillStep))
    assert email.value == LiteralValue(kind="literal", value=EMAIL)
    assert password.value == SecretValue(kind="secret", name="portal_password")
    assert (
        written(out).steps[0].model_dump()["value"]
        == InputValue(kind="input", name="portal_url").model_dump()
    )
    assert scenario.verified[0][1] == {"portal_url": START_URL}


def test_an_input_description_typed_after_the_name_is_written(tmp_path: Path) -> None:
    out = tmp_path / "sign_in.yaml"

    result = Scenario().invoke(
        [START_URL, "--out", str(out)],
        answers="\nportal_url: sign-in page of the portal\n: the account's email\n",
    )

    assert result.exit_code == 0, result.stderr
    assert [(item.name, item.description) for item in written(out).inputs] == [
        ("portal_url", "sign-in page of the portal"),
        ("email_address", "the account's email"),
    ]


def test_three_invalid_answers_fall_back_to_the_proposed_name(
    tmp_path: Path, plain_stdout: PlainStdout
) -> None:
    result = Scenario().invoke(
        [START_URL, "--out", str(tmp_path / "demo.yaml")], answers="A B\nC D\nE F\n\n\n"
    )

    assert result.exit_code == 0, result.stderr
    assert "using the proposed name" in plain_stdout(result)


def test_the_end_of_input_accepts_every_default(tmp_path: Path, plain_stdout: PlainStdout) -> None:
    result = Scenario().invoke([START_URL, "--out", str(tmp_path / "demo.yaml")])

    assert result.exit_code == 0, result.stderr
    assert "(no answer; using the default)" in plain_stdout(result)


def test_no_verify_writes_with_a_warning_and_replays_nothing(
    tmp_path: Path, plain_stdout: PlainStdout
) -> None:
    scenario = Scenario()

    result = scenario.invoke([START_URL, "--out", str(tmp_path / "demo.yaml"), "--no-verify"])

    assert result.exit_code == 0
    assert "WARNING: not verified (--no-verify)" in plain_stdout(result)
    assert scenario.verified == []


def test_a_failed_verification_exits_1_and_keeps_the_file(
    tmp_path: Path, plain_stdout: PlainStdout
) -> None:
    out = tmp_path / "demo.yaml"

    result = Scenario(status=RunStatus.FAILED).invoke([START_URL, "--out", str(out)])

    assert result.exit_code == 1
    assert "NOT VERIFIED" in plain_stdout(result)
    assert out.is_file()


def test_a_missing_secret_exits_2_and_names_its_variable(tmp_path: Path) -> None:
    scenario = Scenario(verify_error=SecretUnavailable("missing", names=["password"]))

    result = scenario.invoke([START_URL, "--out", str(tmp_path / "demo.yaml")])

    assert result.exit_code == 2
    assert "set MENDWORK_SECRET_PASSWORD" in result.stderr


def test_a_broken_browser_during_verification_exits_3(tmp_path: Path) -> None:
    scenario = Scenario(verify_error=BrowserUnavailable("gone"))

    result = scenario.invoke([START_URL, "--out", str(tmp_path / "demo.yaml")])

    assert result.exit_code == 3


def test_an_unusable_recording_exits_2_and_writes_nothing(tmp_path: Path) -> None:
    scenario = Scenario()
    scenario.page.events.clear()
    out = tmp_path / "demo.yaml"

    result = scenario.invoke([START_URL, "--out", str(out)])

    assert result.exit_code == 2
    assert "Recording stopped: nothing was recorded after the start page opened." in result.stderr
    assert "Nothing was written." in result.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (BrowserUnavailable("could not launch Chromium"), 3, "BrowserUnavailable"),
        (KeyboardInterrupt(), 2, "Recording aborted. Nothing was written."),
    ],
)
def test_a_browser_that_will_not_open_or_an_abort_writes_nothing(
    tmp_path: Path, error: BaseException, code: int, message: str
) -> None:
    out = tmp_path / "demo.yaml"

    result = Scenario(open_error=error).invoke([START_URL, "--out", str(out)])

    assert result.exit_code == code
    assert message in result.stderr
    assert not out.exists()


def test_a_file_that_appears_while_recording_is_never_overwritten(tmp_path: Path) -> None:
    scenario = Scenario()
    out = tmp_path / "demo.yaml"

    def appear(fake: FakeBrowser) -> None:
        out.write_text("someone else's\n", encoding="utf-8")

    scenario.page.elements["save"].on_action = appear

    result = scenario.invoke([START_URL, "--out", str(out)])

    assert result.exit_code == 2
    assert "appeared while recording" in result.stderr
    assert out.read_text(encoding="utf-8") == "someone else's\n"


def test_usage_problems_are_none_for_good_arguments(tmp_path: Path) -> None:
    assert usage_problem(START_URL, tmp_path / "download_report.yml") is None


@pytest.mark.asyncio
async def test_ctrl_c_stops_recording_and_a_second_ctrl_c_aborts_it() -> None:
    stop = InterruptStop()

    async def recording() -> None:
        async with stop.connected():
            signal.raise_signal(signal.SIGINT)
            await stop.wait()
            assert stop.requested
            signal.raise_signal(signal.SIGINT)
            await asyncio.Event().wait()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.ensure_future(recording())
