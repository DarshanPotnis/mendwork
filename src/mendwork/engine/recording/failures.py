"""Why a recording cannot continue, as one closed vocabulary.

Every fatal condition ends the recording before anything is written. Interactions that can
simply be repeated are not failures: they are ignored with a notice (``IgnoredReason``).
"""

from enum import StrEnum

from mendwork.engine.errors import RecordingUnusable


class UnusableReason(StrEnum):
    """The causes that end a recording."""

    NO_SELECTOR = "no_selector"
    IDENTITY_UNCONFIRMED = "identity_unconfirmed"
    PAGE_NEVER_STABLE = "page_never_stable"
    NEW_PAGE_OPENED = "new_page_opened"
    BROWSER_NAVIGATION_DURING_STEP = "browser_navigation_during_step"
    PAGE_RESTORED = "page_restored"
    ELEMENT_GONE = "element_gone"
    UNRECORDABLE_TARGET = "unrecordable_target"
    START_URL_UNREACHABLE = "start_url_unreachable"
    NOTHING_RECORDED = "nothing_recorded"
    INVALID_WORKFLOW = "invalid_workflow"
    PROTOCOL_VIOLATION = "protocol_violation"


def unusable(reason: UnusableReason, message: str, **context: object) -> RecordingUnusable:
    """The error that ends a recording, with its reason in the context."""
    return RecordingUnusable(message, reason=reason.value, **context)
