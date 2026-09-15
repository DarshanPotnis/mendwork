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


class TargetDrifted(MendworkError):
    """The selectors agree on one element, but it is not the element that was recorded.

    Its role, tag, type, or accessible name differs from the fingerprint, or Playwright
    could not confirm the identity that was computed. Acting on it could do something
    other than what the step intends ("Export ledger" relabelled "Delete ledger").
    """


class HealAbstained(MendworkError):
    """The heal ladder examined the page and found nothing it could safely act on.

    Abstaining is a correct outcome, not a malfunction: acting on a guess could click the
    wrong control. ``reason`` names why (below the threshold, too close to a look-alike,
    refused by a safety rule, no checkpoint to verify with, and so on).
    """


class ApprovalRequired(MendworkError):
    """A heal was found for an irreversible step, which never acts on a heal without approval."""


class NeedsReview(MendworkError):
    """An irreversible action ran on a healed target and its checkpoints did not pass.

    It is never retried: repeating it could pay, send, or delete twice.
    """


class TargetNotActionable(MendworkError):
    """The target was found and verified, but cannot receive the step's action."""


class PageNeverStable(MendworkError):
    """The page kept changing, so the target could not be verified safely.

    The element may well be present: the problem is the page, whose DOM changed during
    every attempt to read a consistent picture of it.
    """


class CheckpointFailed(MendworkError):
    """A step's checkpoint did not pass, so the step is not considered successful."""


class NavigationError(MendworkError):
    """The browser could not reach or load a page, or an action opened another page."""


class RunTimedOut(MendworkError):
    """The run exceeded its overall time limit."""


class RunCancelled(MendworkError):
    """The run was interrupted (Ctrl+C or SIGTERM) before it finished.

    ``interruption`` says how, and ``irreversible_steps`` lists irreversible actions dispatched
    before it, which make the run need review rather than be cancelled (ADR 0011).
    """


class RunBusy(MendworkError):
    """Another process is running or resuming this run, so this one may not touch it."""


class UnknownRun(MendworkError):
    """No run record exists for the run id, or it cannot be read as one."""


class ProposalNotPending(MendworkError):
    """The proposal is not waiting for a decision.

    ``reason`` says why: an unknown proposal, one already decided, one a later proposal replaced,
    or a run that is not awaiting approval. Nothing was recorded.
    """


class RunNotResumable(MendworkError):
    """An approval could not resume the run, so nothing was recorded; ``reason`` says why."""


class ApprovalStale(MendworkError):
    """When the run resumed, the page no longer showed the approved element; nothing acted.

    ``stale_reason`` says what no longer matched. A fresh run makes a fresh proposal.
    """


class SecretUnavailable(MendworkError):
    """A secret the workflow declares could not be resolved; it is missing or empty."""


class InfrastructureError(MendworkError):
    """Something Mendwork depends on failed, rather than the workflow or the site."""


class BrowserUnavailable(InfrastructureError):
    """The browser could not be launched, or closed while a run was using it."""


class ArtifactStoreUnavailable(InfrastructureError):
    """Run artifacts could not be written."""


class WorkflowStoreUnavailable(InfrastructureError):
    """Workflow versions or pending patches could not be read or written."""


class AuditLogCorrupt(InfrastructureError):
    """The audit log cannot be read or written, or its chain of entries is broken.

    No decision is recorded while it is: an approval must never rest on a log that cannot be
    trusted. ``path`` names the file.
    """


class RecordingUnusable(MendworkError):
    """A recording cannot produce a workflow that replays, so it ends and nothing is written.

    ``reason`` names the cause: no selector survived verification, an identity Playwright
    did not confirm, a page that never stopped changing, a new tab or window, a browser
    navigation in the middle of a step, a page restored from the back-forward cache, or an
    element that vanished before its step could be recorded.
    """


class ProviderError(MendworkError):
    """A model provider call failed, timed out, or returned an unusable response.

    ``reason`` names the cause (``timeout``, ``connection``, ``rate_limited``, ``http_status``,
    ``circuit_open``, ``response_too_large``, ``malformed_response``). When a request was sent,
    ``usage`` holds the call's ModelUsage.
    """


class ModelOutputInvalid(ProviderError):
    """The model replied, but not in the one shape Rung 3 accepts.

    ``problem`` says what was wrong in fixed words, and ``excerpt`` holds the start of the
    reply so a repair request can show the model what it sent. The reply is never put in the
    message.
    """


class PolicyViolation(MendworkError):
    """A safety policy refused the requested operation."""


class EgressBlocked(PolicyViolation):
    """The egress policy refused a navigation or a connection (ADR 0011).

    ``rule`` names why (``scheme``, ``malformed_url``, ``not_allowlisted``, ``blocked_address``),
    with the host and, for an address, its range; ``blocks`` lists every refusal. It is never
    retried or healed: the run was pointed somewhere it must not go, and a person should look.
    """


class BudgetExceeded(MendworkError):
    """A run or a workspace exhausted its model-call budget, or the count could not be read.

    ``scope`` is ``run`` or ``day``, with ``limit`` and, for a day, ``resets_at``.
    """


class _IssuesError(MendworkError):
    """An error that reports every problem found at once, as ``issues``."""

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


class WorkflowValidationError(_IssuesError):
    """A workflow definition is structurally invalid or internally inconsistent.

    Every problem found is reported at once, so fixing a file is one edit session rather
    than a loop of one error per attempt.
    """


class RunInputError(_IssuesError):
    """The inputs supplied for a run do not match the workflow's declarations.

    Unknown names, missing required inputs, and invalid values are all reported together,
    each located at ``inputs.<name>``. Messages never echo the supplied values.
    """


class UnsupportedSchemaVersion(WorkflowValidationError):
    """A workflow file declares a ``schema_version`` this build of Mendwork cannot read."""


class VersionConflict(MendworkError):
    """A publish conflicts with stored versions: the number is taken or the parent is missing."""


class UnknownWorkflowVersion(MendworkError):
    """The workflow store has no versions of the workflow, or not the version asked for."""
