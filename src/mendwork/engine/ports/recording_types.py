"""Values exchanged with a recording browser: what a person did, described without values.

Captures name a document and an element by opaque keys the page assigned; they never
carry what was typed. Element facts are read without field values, and the one way a
field's content reaches the engine is ``read_field_text``, which the engine calls only for
fields that are not credential fields and which reports a masked field instead of reading it.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.recording import IgnoredReason


class CaptureRef(DomainModel):
    """An interaction's place in its document's message sequence, and the element it used."""

    document: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    element: int | None = Field(default=None, ge=1)
    """The page's key for the element; None for a key pressed with nothing focused."""


class ClickCapture(DomainModel):
    """A plain click the page held back; the recorder performs it."""

    kind: Literal["click"] = "click"
    ref: CaptureRef


class FillCapture(DomainModel):
    """A field whose content the person changed and committed."""

    kind: Literal["fill"] = "fill"
    ref: CaptureRef


class SelectCapture(DomainModel):
    """A select whose chosen option the person changed."""

    kind: Literal["select"] = "select"
    ref: CaptureRef


class PressKey(StrEnum):
    """The keys recorded as steps. Every other key is part of typing or moving focus."""

    ENTER = "Enter"
    ESCAPE = "Escape"
    SPACE = "Space"


class PressCapture(DomainModel):
    """A key the page held back; the recorder performs it."""

    kind: Literal["press"] = "press"
    ref: CaptureRef
    key: PressKey


class IgnoredCapture(DomainModel):
    """An interaction the page held back and dropped, because it cannot be recorded."""

    kind: Literal["ignored"] = "ignored"
    reason: IgnoredReason


class NavigationInitiator(StrEnum):
    """Who started a navigation, which decides whether it can be a step of its own."""

    PAGE = "page"
    """The page: a link, a form, a script, or a meta refresh."""
    BROWSER = "browser"
    """The browser: the address bar, back, forward, or reload."""


class NavigationRecord(DomainModel):
    """A new document committed in the main frame."""

    sequence: int = Field(ge=1)
    url: str
    initiator: NavigationInitiator


class NavigationCommitted(DomainModel):
    """The main frame committed a new document."""

    kind: Literal["navigated"] = "navigated"
    record: NavigationRecord


class PageRestored(DomainModel):
    """A document came back from the back-forward cache with its old state."""

    kind: Literal["restored"] = "restored"


class ProtocolViolation(DomainModel):
    """The page sent a message the recorder does not understand, so it cannot be trusted."""

    kind: Literal["protocol_violation"] = "protocol_violation"


class SessionClosed(DomainModel):
    """The person closed the browser window."""

    kind: Literal["closed"] = "closed"


Capture = ClickCapture | FillCapture | SelectCapture | PressCapture
PageEvent = Annotated[
    ClickCapture
    | FillCapture
    | SelectCapture
    | PressCapture
    | IgnoredCapture
    | NavigationCommitted
    | PageRestored
    | ProtocolViolation
    | SessionClosed,
    Field(discriminator="kind"),
]


class AncestorFacts(DomainModel):
    """An ancestor of a target, described for choosing a selector scope."""

    tag: str
    role: str | None = None
    name: str = ""
    data_testid: str | None = None
    id: str | None = None
    row_header: str | None = None
    """For a table row, the text of its row header cell."""


class MaskedField(DomainModel):
    """The field turned out to be masked, so its content was not read."""

    kind: Literal["masked"] = "masked"


class FieldValue(DomainModel):
    """A non-credential field's content, or a select's chosen option label."""

    kind: Literal["value"] = "value"
    value: str


FieldText = Annotated[MaskedField | FieldValue, Field(discriminator="kind")]


class Landmark(DomainModel):
    """A visible heading or landmark: its role and accessible name."""

    role: str
    name: str


class PageObservation(DomainModel):
    """The page at one moment: where it is and what a checkpoint could look for."""

    url: str
    title: str = ""
    navigation: int = Field(ge=0)
    """How many main-frame documents had committed when the observation began."""
    landmarks: tuple[Landmark, ...] = ()
    """Visible headings and landmarks, in document order."""
    live_texts: tuple[str, ...] = ()
    """The text of visible live regions (status, alert, log, aria-live), in document order."""
