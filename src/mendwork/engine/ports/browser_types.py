"""Values exchanged with the BrowserPort: plain data, never browser-library objects.

An ``ElementRef`` is an opaque name for one DOM node the adapter has pinned. Actions take a
ref rather than a selector, so an action always reaches the node that was verified and
never whatever a selector happens to match a moment later.
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, NewType

from pydantic import Field, SecretStr

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.runs import TraceWithheldReason
from mendwork.engine.domain.targets import IdentityReport

ElementRef = NewType("ElementRef", str)
WatchId = NewType("WatchId", str)


class DomEpoch(DomainModel):
    """A point in a page's history: which document, and how many DOM mutations it has seen."""

    document: str
    mutations: int = Field(ge=0)


class Settling(DomainModel):
    """The result of waiting for the page to settle."""

    quiet: bool
    """Whether the DOM went quiet for the requested frames before the timeout."""
    epoch: DomEpoch


class UniqueMatch(DomainModel):
    """What one selector found, counting only visible elements at every scope level."""

    level_counts: tuple[int, ...]
    """Matches per level, outermost first, stopping at the first level that was not 1."""
    element: ElementRef | None = None
    """The pinned element, present only when every level matched exactly one."""


class ElementIdentity(DomainModel):
    """An element's identity as computed in the page, optionally confirmed by Playwright."""

    tag: str
    input_type: str | None = None
    """The element's ``type`` attribute, lower-cased, if it has one."""
    role: str | None = None
    name: str
    confirmed: bool | None = None
    """True when Playwright's role locator, given this role and name, finds this element;
    False when it does not or the name could not be computed faithfully; None when there
    was nothing to confirm (no role, or confirmation not requested)."""

    def report(self) -> IdentityReport:
        """The identity as run evidence."""
        return IdentityReport(
            tag=self.tag,
            input_type=self.input_type,
            role=self.role,
            name=self.name,
            confirmed=self.confirmed,
        )


class Actionability(DomainModel):
    """Whether an element can receive an action right now."""

    attached: bool
    visible: bool
    enabled: bool
    editable: bool


class NavigationOutcome(DomainModel):
    """Where a navigation landed and the main document's HTTP status, if there was one."""

    url: str
    status: int | None = None


class PlainText(DomainModel):
    """A value to type that may be recorded by the browser's tooling."""

    kind: Literal["plain"] = "plain"
    value: str


class SecretText(DomainModel):
    """A secret value to type; the adapter must keep it out of every trace and artifact."""

    kind: Literal["secret"] = "secret"
    value: SecretStr


FillText = Annotated[PlainText | SecretText, Field(discriminator="kind")]


class EqualsText(DomainModel):
    """The field must hold exactly this value."""

    kind: Literal["equals"] = "equals"
    value: str


class NonEmpty(DomainModel):
    """The field must hold something; used for secrets, which are never compared."""

    kind: Literal["non_empty"] = "non_empty"


FieldExpectation = Annotated[EqualsText | NonEmpty, Field(discriminator="kind")]


class FieldValueCheck(DomainModel):
    """The outcome of a field value check. It never carries the field's value."""

    matches: bool
    empty: bool


class WatchKind(StrEnum):
    """Browser events a step can observe, which must be watched before its action runs."""

    DOWNLOAD = "download"
    RESPONSE = "response"


class DownloadObservation(DomainModel):
    """A download that started after its watch began."""

    suggested_filename: str
    path: Path | None = None
    """Where the completed file was saved, when it completed."""
    failure: str | None = None


class ResponseObservation(DomainModel):
    """A network response: its URL and status only, never its body."""

    url: str
    status: int


class TraceSaved(DomainModel):
    """A failure trace was written and scanned clean of registered secrets."""

    kind: Literal["saved"] = "saved"
    path: Path


class TraceNotSaved(DomainModel):
    """A failure trace was deliberately not kept."""

    kind: Literal["withheld"] = "withheld"
    reason: TraceWithheldReason


class TraceDisabled(DomainModel):
    """Tracing is turned off for this run."""

    kind: Literal["disabled"] = "disabled"


TraceExport = Annotated[TraceSaved | TraceNotSaved | TraceDisabled, Field(discriminator="kind")]
