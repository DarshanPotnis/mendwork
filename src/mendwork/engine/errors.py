"""Error hierarchy for the engine.

Every error carries structured context so a failure can be logged, stored on a run
record, and shown to a user as key-value evidence, instead of being flattened into
a message string that later code has to parse back apart.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

Location = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One problem found in a workflow document, addressed by its path inside the document.

    The engine knows paths but not files; an adapter that read the document from disk
    fills in ``line`` and ``column`` so a person can jump straight to the mistake.
    """

    location: Location
    message: str
    line: int | None = None
    column: int | None = None
    step_id: str | None = None
    """The id of the step the location is inside, when there is one, to orient the reader."""

    @property
    def path(self) -> str:
        """The location rendered the way people write it, such as ``steps[3].value``."""
        return format_location(self.location)


def format_location(location: Location) -> str:
    """Render a document path as dotted keys with bracketed indexes."""
    rendered = ""
    for part in location:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else part
    return rendered


def _rebuild_error(
    error_type: type["MendworkError"], message: str, context: dict[str, object]
) -> "MendworkError":
    """Reconstruct an error during unpickling, preserving its context."""
    return error_type(message, **context)


class MendworkError(Exception):
    """Base class for every error raised by Mendwork."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self._message = message
        self._context: dict[str, object] = dict(context)

    @property
    def message(self) -> str:
        """The human-readable summary of what went wrong."""
        return self._message

    @property
    def context(self) -> Mapping[str, object]:
        """Structured fields describing the failure, safe to attach to a log event."""
        return MappingProxyType(self._context)

    def __reduce__(self) -> tuple[object, ...]:
        # Exception's default __reduce__ replays only self.args, which would drop the
        # context; runs cross a process boundary (worker to API) from Phase 10 onward.
        return (_rebuild_error, (type(self), self._message, dict(self._context)))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self._message!r}, context={self._context!r})"


class TargetNotFound(MendworkError):
    """No element on the page matched a step's target."""


class AmbiguousTarget(MendworkError):
    """More than one element matched a step's target, so acting would be a guess."""


class CheckpointFailed(MendworkError):
    """A step's checkpoint did not pass, so the step is not considered successful."""


class NavigationError(MendworkError):
    """The browser could not reach or load a page."""


class ProviderError(MendworkError):
    """A model provider call failed, timed out, or returned an unusable response."""


class PolicyViolation(MendworkError):
    """A safety policy refused the requested operation."""


class BudgetExceeded(MendworkError):
    """A run or a workspace exhausted its model-call budget."""


class WorkflowValidationError(MendworkError):
    """A workflow definition is structurally invalid or internally inconsistent.

    Every problem found is reported at once, as ``issues``, so fixing a file is one edit
    session rather than a loop of one error per attempt.
    """

    def __init__(
        self, message: str, *, issues: Iterable[ValidationIssue] = (), **context: object
    ) -> None:
        self._issues = tuple(issues)
        # Kept in the context too, so pickling (which replays the context) restores them.
        if self._issues:
            context = {"issues": self._issues, **context}
        super().__init__(message, **context)

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        """The individual problems, in document order where the document gave one."""
        return self._issues


class UnsupportedSchemaVersion(WorkflowValidationError):
    """A workflow file declares a ``schema_version`` this build of Mendwork cannot read."""


class VersionConflict(MendworkError):
    """A publish conflicts with stored versions: the number is taken or the parent is missing."""
