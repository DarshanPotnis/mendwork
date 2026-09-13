"""``mendwork record``: do a task once in a browser, get a workflow that is proven to replay.

Flow: check the arguments (nothing opens on bad usage) → record until Ctrl+C or the window
closes → print the step summary → name secrets and inputs → validate and write the file,
never over an existing one → replay it in a fresh browser (unless ``--no-verify``).

Exit codes: 0 recorded and verified (or recorded with ``--no-verify``); 1 the verification
replay failed at a step; 2 invalid usage, an unusable recording, or a secret missing for
verification; 3 infrastructure. Logs go to stderr; everything else to stdout. The real
browser, terminal, and clock are composed in ``record_runtime``.
"""

import asyncio
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol, TextIO

import typer
from pydantic import ValidationError

from mendwork.adapters.secrets_env.naming import secret_variable_name
from mendwork.adapters.storage_fs.file_ops import write_new_file
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.arguments import parse_input_arguments
from mendwork.apps.cli.exit_codes import ExitCode, exit_code_for
from mendwork.apps.cli.record_output import (
    HumanRecordingProgress,
    render_recording_summary,
    written_line,
)
from mendwork.apps.cli.record_prompts import LinePrompter, decide_names
from mendwork.apps.cli.validate import format_issue, format_problems
from mendwork.apps.cli.wiring import recording_config
from mendwork.engine.domain.identifiers import SLUG_DESCRIPTION, SecretName, is_slug
from mendwork.engine.domain.recording import Recording
from mendwork.engine.domain.runs import Run
from mendwork.engine.domain.values import check_http_url
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import (
    InfrastructureError,
    RecordingUnusable,
    RunInputError,
    SecretUnavailable,
    WorkflowValidationError,
)
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.recording import RecordingLauncher, StopSignal
from mendwork.engine.ports.timer import Timer
from mendwork.engine.recording.assembly import assemble
from mendwork.engine.recording.recorder import Recorder
from mendwork.settings import Settings

WORKFLOW_SUFFIXES = (".yaml", ".yml")


class RecorderFactory(Protocol):
    """Opens the browser a recording happens in."""

    def __call__(
        self, settings: Settings, slow_mo_ms: int | None
    ) -> AbstractAsyncContextManager[RecordingLauncher]:
        """A launcher, closed when the context exits."""
        ...


class Verifier(Protocol):
    """Replays a freshly written workflow to prove it works."""

    async def __call__(
        self,
        version: WorkflowVersion,
        inputs: Mapping[str, str],
        settings: Settings,
        *,
        slow_mo_ms: int | None,
        stdout: TextIO,
    ) -> Run:
        """The verification run. Raises SecretUnavailable before starting if a secret is missing."""
        ...


class StopControl(StopSignal, Protocol):
    """A stop signal that can be connected to the terminal while recording runs."""

    def connected(self) -> AbstractAsyncContextManager[None]:
        """Listen for the person's stop request while the context is open."""
        ...


@dataclass(frozen=True, slots=True)
class RecordDependencies:
    """What ``mendwork record`` is composed of; tests replace the browser-facing parts."""

    open_recorder: RecorderFactory
    verify: Verifier
    stop: Callable[[], StopControl]
    clock: Clock
    timer: Callable[[], Timer]
    randomness: Callable[[], RandomSource]


def build_record_command(dependencies: RecordDependencies) -> Callable[..., None]:
    """The ``record`` command, composed of the given dependencies."""

    def record(
        start_url: Annotated[str, typer.Argument(help="The page to start recording on.")],
        out: Annotated[
            Path,
            typer.Option("--out", dir_okay=False, help="Workflow YAML to write; must not exist."),
        ],
        verify: Annotated[
            bool,
            typer.Option(
                "--verify/--no-verify",
                help="Replay the recording to prove it works. Skip it only when replaying "
                "would repeat a real side effect.",
            ),
        ] = True,
        inputs: Annotated[
            list[str] | None,
            typer.Option(
                "--input",
                "-i",
                metavar="NAME=VALUE",
                help="Make every recorded value equal to VALUE the run input NAME.",
            ),
        ] = None,
        slow_mo: Annotated[
            int | None,
            typer.Option(
                "--slow-mo", min=0, max=10_000, metavar="MS", help="Pause after browser operations."
            ),
        ] = None,
    ) -> None:
        """Record a workflow by doing it once in a browser, then verify it by replaying it."""
        command = RecordCommand(dependencies, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        code = command.execute(
            start_url=start_url, out=out, verify=verify, inputs=inputs or [], slow_mo_ms=slow_mo
        )
        raise typer.Exit(code=code)

    return record


class RecordCommand:
    """One ``mendwork record`` invocation."""

    def __init__(
        self, dependencies: RecordDependencies, *, stdin: TextIO, stdout: TextIO, stderr: TextIO
    ) -> None:
        self._deps = dependencies
        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr

    def execute(
        self,
        *,
        start_url: str,
        out: Path,
        verify: bool,
        inputs: list[str],
        slow_mo_ms: int | None,
    ) -> int:
        """Run the whole flow and return the exit code."""
        try:
            settings = Settings()
        except ValidationError as error:
            return self._fail(ExitCode.INVALID, f"invalid configuration: {error}")
        problem = usage_problem(start_url, out)
        if problem is not None:
            return self._fail(ExitCode.INVALID, problem)
        try:
            overrides = list(parse_input_arguments(inputs).items())
        except RunInputError as error:
            lines = [format_issue("--input", issue) for issue in error.issues]
            return self._fail(ExitCode.INVALID, "\n".join(lines))
        self._say(
            f"Recording from {start_url}. Do the task in the browser window, then press Ctrl+C "
            "here (or close the window) to stop.\n"
        )
        try:
            recording = asyncio.run(self._capture(settings, start_url, slow_mo_ms))
        except RecordingUnusable as error:
            return self._fail(
                ExitCode.INVALID, f"Recording stopped: {error.message}. Nothing was written."
            )
        except InfrastructureError as error:
            return self._fail(ExitCode.INFRASTRUCTURE, f"{type(error).__name__}: {error.message}")
        except (KeyboardInterrupt, asyncio.CancelledError):
            return self._fail(ExitCode.INVALID, "Recording aborted. Nothing was written.")
        return self._finish(settings, recording, out, verify, overrides, slow_mo_ms)

    def _finish(
        self,
        settings: Settings,
        recording: Recording,
        out: Path,
        verify: bool,
        overrides: list[tuple[str, str]],
        slow_mo_ms: int | None,
    ) -> int:
        self._say(render_recording_summary(recording))
        try:
            decisions = decide_names(
                recording.steps, overrides, LinePrompter(self._stdin, self._stdout), self._stdout
            )
            version = assemble(recording, decisions, workflow_id=out.stem, clock=self._deps.clock)
        except KeyboardInterrupt:
            return self._fail(ExitCode.INVALID, "Recording aborted. Nothing was written.")
        except RecordingUnusable as error:
            return self._fail(ExitCode.INVALID, f"{error.message}. Nothing was written.")
        codec = WorkflowYamlCodec(max_bytes=settings.workflow_max_bytes)
        data = codec.encode(version)
        try:
            codec.decode(data, source=str(out))
        except WorkflowValidationError as error:
            return self._fail(ExitCode.INVALID, format_problems(str(out), error))
        try:
            asyncio.run(asyncio.to_thread(write_new_file, out, data))
        except FileExistsError:
            return self._fail(
                ExitCode.INVALID, f"{out} appeared while recording; nothing was written"
            )
        except OSError as error:
            return self._fail(ExitCode.INFRASTRUCTURE, f"could not write {out}: {error.strerror}")
        self._say("\n" + written_line(str(out), len(version.steps), decisions))
        if not verify:
            self._say(
                "WARNING: not verified (--no-verify). The workflow has not been replayed; run "
                f"`mendwork run {out}` when replaying it is safe."
            )
            return ExitCode.SUCCEEDED
        values: dict[str, str] = {decision.name: decision.value for decision in decisions.inputs}
        return self._verify(settings, version, values, out, slow_mo_ms)

    def _verify(
        self,
        settings: Settings,
        version: WorkflowVersion,
        values: Mapping[str, str],
        out: Path,
        slow_mo_ms: int | None,
    ) -> int:
        self._say("\nVerifying: replaying the recorded workflow in a fresh browser.\n")
        try:
            run = asyncio.run(
                self._deps.verify(
                    version, values, settings, slow_mo_ms=slow_mo_ms, stdout=self._stdout
                )
            )
        except SecretUnavailable as error:
            names = error.context.get("names")
            listed = [str(name) for name in names] if isinstance(names, list) else []
            lines = [f"NOT VERIFIED: {error.message}"]
            lines.extend(f"  set {secret_variable_name(SecretName(name))}" for name in listed)
            lines.append(f"then run `mendwork run {out}`")
            return self._fail(ExitCode.INVALID, "\n".join(lines))
        except InfrastructureError as error:
            return self._fail(ExitCode.INFRASTRUCTURE, f"{type(error).__name__}: {error.message}")
        code = exit_code_for(run)
        if code is ExitCode.SUCCEEDED:
            self._say(f"VERIFIED: {out} replayed successfully.")
        else:
            self._say(f"NOT VERIFIED: {out} was written, but replaying it failed.")
        return code

    async def _capture(
        self, settings: Settings, start_url: str, slow_mo_ms: int | None
    ) -> Recording:
        stop = self._deps.stop()
        async with self._deps.open_recorder(settings, slow_mo_ms) as launcher:
            recorder = Recorder(
                launcher=launcher,
                observer=HumanRecordingProgress(self._stdout),
                timer=self._deps.timer(),
                randomness=self._deps.randomness(),
                config=recording_config(settings),
            )
            async with stop.connected():
                return await recorder.record(start_url, stop)

    def _say(self, text: str) -> None:
        self._stdout.write(text + "\n")
        self._stdout.flush()

    def _fail(self, code: ExitCode, message: str) -> int:
        self._stderr.write(message + "\n")
        self._stderr.flush()
        return code


def usage_problem(start_url: str, out: Path) -> str | None:
    """Why the arguments cannot be used, before any browser opens; None if they can."""
    try:
        check_http_url(start_url)
    except ValueError as error:
        return f"start URL {error}"
    if out.suffix not in WORKFLOW_SUFFIXES:
        return f"--out must end in {' or '.join(WORKFLOW_SUFFIXES)}"
    if out.exists():
        return f"--out {out} already exists; recording never overwrites a file"
    if not out.parent.is_dir():
        return f"--out {out}: the directory {out.parent} does not exist"
    if not is_slug(out.stem):
        return f"--out {out}: the file name becomes the workflow id, which {SLUG_DESCRIPTION}"
    return None
