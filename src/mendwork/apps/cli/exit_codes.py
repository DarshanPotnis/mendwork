"""Process exit codes for ``mendwork run``, so scripts can tell failures apart."""

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


def exit_code_for(run: Run) -> ExitCode:
    """The exit code for a run that started."""
    if run.status is RunStatus.SUCCEEDED:
        return ExitCode.SUCCEEDED
    if run.status in {RunStatus.AWAITING_APPROVAL, RunStatus.NEEDS_REVIEW}:
        return ExitCode.NEEDS_PERSON
    if run.error is not None and run.error.category is ErrorCategory.INFRASTRUCTURE:
        return ExitCode.INFRASTRUCTURE
    return ExitCode.STEP_FAILED
