"""Process exit codes for commands that execute a run, so scripts can tell outcomes apart."""

from enum import IntEnum

from mendwork.engine.domain.runs import ErrorCategory, Run, RunStatus


class ExitCode(IntEnum):
    """What a run's exit status means."""

    SUCCEEDED = 0
    """Every step ran and every checkpoint passed."""
    STEP_FAILED = 1
    """The run stopped at a step: a target, checkpoint, navigation, or time limit."""
    INVALID = 2
    """The workflow, inputs, secrets, settings, or command line were invalid; nothing ran."""
    INFRASTRUCTURE = 3
    """Mendwork's own machinery failed, such as the browser or the artifact store."""
    NEEDS_PERSON = 4
    """The run stopped for a person: a heal awaits approval, or an action needs review."""
    CANCELLED = 130
    """The run was interrupted before it finished and dispatched nothing irreversible; 130 is the
    shell's code for a process ended by Ctrl+C."""


def exit_code_for(run: Run) -> ExitCode:
    """The exit code for a run that started.

    An interrupted run that needs review exits 4 like any other run stopped for a person, so a
    script alerting a person on 4 never misses one.
    """
    if run.status is RunStatus.SUCCEEDED:
        return ExitCode.SUCCEEDED
    if run.status in {RunStatus.AWAITING_APPROVAL, RunStatus.NEEDS_REVIEW}:
        return ExitCode.NEEDS_PERSON
    if run.status is RunStatus.CANCELLED:
        return ExitCode.CANCELLED
    if run.error is not None and run.error.category is ErrorCategory.INFRASTRUCTURE:
        return ExitCode.INFRASTRUCTURE
    return ExitCode.STEP_FAILED
