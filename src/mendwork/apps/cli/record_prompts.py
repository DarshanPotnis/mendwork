"""Naming prompts in a plain terminal: one line per question, Enter accepts the default.

No prompt library: questions are written to stdout and answers read a line at a time from
stdin, which also works when stdin is a pipe or a test runner's input. At the end of input,
every remaining question takes its default. The rules for names live in the engine's
NamingSession; this module only asks and relays.
"""

from collections.abc import Callable, Sequence
from functools import partial
from typing import Final, TextIO

from mendwork.engine.domain.recording import DraftStep
from mendwork.engine.recording.naming import KEEP_LITERAL, Decisions, NamingSession

ATTEMPTS: Final = 3


class LinePrompter:
    """Asks a question on one line and reads the answer from the next line of input."""

    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self._stdin = stdin
        self._stdout = stdout

    def ask(self, question: str, default: str) -> str:
        """The answer, stripped; empty means the default. End of input also means the default."""
        self._stdout.write(f"{question} [{default}]: ")
        self._stdout.flush()
        line = self._stdin.readline()
        if not line:
            self._stdout.write("(no answer; using the default)\n")
            return ""
        if not self._stdin.isatty():
            # On a terminal the person's Enter ends the line; piped answers are echoed instead,
            # so every question still starts on a line of its own. Answers are only names.
            self._stdout.write(line if line.endswith("\n") else line + "\n")
        return line.strip()


def decide_names(
    steps: Sequence[DraftStep],
    overrides: Sequence[tuple[str, str]],
    prompter: LinePrompter,
    stdout: TextIO,
) -> Decisions:
    """Ask for every secret name and every proposed input; return the decisions."""
    session = NamingSession(steps, overrides)
    for warning in session.warnings:
        stdout.write(f"warning: {warning}\n")
    if session.secret_proposals:
        stdout.write(
            "\nSecrets: a run reads each value from MENDWORK_SECRET_<NAME>. Enter accepts the "
            "proposed name.\n"
        )
    for secret in session.secret_proposals:
        _ask(
            prompter,
            stdout,
            f"  {secret.prompt} · secret name",
            suggest=partial(session.suggested_secret_name, secret),
            choose=partial(session.name_secret, secret),
        )
    if session.input_proposals:
        stdout.write(
            "\nInputs: Enter accepts the proposed name and description, type 'name' or "
            f"'name: description', or '{KEEP_LITERAL}' to keep the value in the file.\n"
        )
    for proposal in session.input_proposals:
        _ask(
            prompter,
            stdout,
            f"  {proposal.prompt} · input",
            suggest=partial(session.suggested_input_answer, proposal),
            choose=partial(session.name_input, proposal),
        )
    return session.decisions()


def _ask(
    prompter: LinePrompter,
    stdout: TextIO,
    question: str,
    *,
    suggest: Callable[[], str],
    choose: Callable[[str], str | None],
) -> None:
    for _ in range(ATTEMPTS):
        problem = choose(prompter.ask(question, suggest()))
        if problem is None:
            return
        stdout.write(f"    {problem}\n")
    stdout.write("    using the proposed name\n")
    choose("")
