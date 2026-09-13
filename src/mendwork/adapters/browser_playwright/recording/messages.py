"""What recorder.js sends and what the recording page scripts reply, validated strictly.

No message model has a field for anything a person typed, and every model forbids unknown
fields. A page script that tried to send a value would be rejected, and the recording would
end, rather than the value quietly reaching Mendwork.
"""

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel

from mendwork.engine.domain.recording import IgnoredReason
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.ports.recording_types import (
    AncestorFacts,
    Box,
    CaptureRef,
    ClickCapture,
    ElementFacts,
    FieldText,
    FieldValue,
    FillCapture,
    IgnoredCapture,
    MaskedField,
    PageEvent,
    PageRestored,
    PressCapture,
    PressKey,
    SelectCapture,
)

_DOCUMENT_TOKEN: Final = r"^[0-9a-f]{32}$"  # noqa: S105 - a pattern for a document id, not a secret


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class _Message(_Strict):
    document: str = Field(pattern=_DOCUMENT_TOKEN)
    sequence: int = Field(ge=1)


class ElementMessage(_Message):
    """A click, a committed fill, or a changed select, on one element."""

    type: Literal["click", "fill", "select"]
    element: int = Field(ge=1)


class PressMessage(_Message):
    """A key held back by the page."""

    type: Literal["press"]
    element: int | None = Field(ge=1)
    key: Literal["Enter", "Escape", "Space"]


class IgnoredMessage(_Message):
    """An interaction held back and dropped."""

    type: Literal["ignored"]
    reason: Literal["modified_click", "modified_key", "double_click", "file_input", "frame", "busy"]


class RestoredMessage(_Message):
    """A document restored from the back-forward cache."""

    type: Literal["restored"]


PageMessage = Annotated[
    ElementMessage | PressMessage | IgnoredMessage | RestoredMessage, Field(discriminator="type")
]
PAGE_MESSAGE: Final[TypeAdapter[PageMessage]] = TypeAdapter(PageMessage)


def is_gesture(message: PageMessage) -> bool:
    """Whether the page waits for this message to be recorded before it lets input through."""
    return message.type in {"click", "press"}


def to_event(message: PageMessage) -> PageEvent:
    """The engine's page event for a validated message."""
    match message:
        case ElementMessage():
            ref = CaptureRef(
                document=message.document, sequence=message.sequence, element=message.element
            )
            if message.type == "click":
                return ClickCapture(ref=ref)
            if message.type == "fill":
                return FillCapture(ref=ref)
            return SelectCapture(ref=ref)
        case PressMessage():
            ref = CaptureRef(
                document=message.document, sequence=message.sequence, element=message.element
            )
            return PressCapture(ref=ref, key=PressKey(message.key))
        case IgnoredMessage():
            return IgnoredCapture(reason=IgnoredReason(message.reason))
        case RestoredMessage():
            return PageRestored()


class _Reply(_Strict):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class BoxReply(_Reply):
    x: float
    y: float
    width: float
    height: float


class FactsReply(_Reply):
    """What element_facts.js returns."""

    tag: str
    id: str | None
    name: str | None
    type: str | None
    autocomplete: str | None
    placeholder: str | None
    aria_label: str | None
    test_id: str | None
    href: str | None
    label_text: str | None
    text: str | None
    own_text: str | None
    nearby_text: list[str]
    structural_path: str
    box: BoxReply | None
    text_entry: bool
    masked: bool
    in_form: bool
    form_submit: bool
    form_has_password: bool

    def facts(self) -> ElementFacts:
        """The engine's element facts."""
        box = self.box
        return ElementFacts(
            tag=self.tag,
            id=self.id,
            name=self.name,
            type=self.type,
            autocomplete=self.autocomplete,
            placeholder=self.placeholder,
            aria_label=self.aria_label,
            data_testid=self.test_id,
            href=self.href,
            label_text=self.label_text,
            text=self.text,
            own_text=self.own_text,
            nearby_text=tuple(self.nearby_text),
            structural_path=self.structural_path,
            box=None if box is None else Box(x=box.x, y=box.y, width=box.width, height=box.height),
            text_entry=self.text_entry,
            masked=self.masked,
            in_form=self.in_form,
            form_submit=self.form_submit,
            form_has_password=self.form_has_password,
        )


class ScopeReply(_Reply):
    """One item of what scope_facts.js returns."""

    tag: str
    id: str | None
    test_id: str | None
    row_header: str | None

    def ancestor(self, identity: ElementIdentity | None) -> AncestorFacts:
        """The engine's ancestor facts, with the ancestor's identity when it was readable."""
        return AncestorFacts(
            tag=self.tag,
            role=identity.role if identity is not None else None,
            name=identity.name if identity is not None else "",
            data_testid=self.test_id,
            id=self.id,
            row_header=self.row_header,
        )


SCOPE_REPLIES: Final[TypeAdapter[list[ScopeReply]]] = TypeAdapter(list[ScopeReply])


class FieldTextReply(_Reply):
    """What field_text.js returns."""

    masked: bool
    value: str | None

    def field_text(self) -> FieldText:
        """A masked field, or the content of one that is not."""
        if self.masked:
            return MaskedField()
        return FieldValue(value=self.value or "")


class ControlReply(_Reply):
    """What recorder_control.js returns."""

    document: str | None
    sequence: int = Field(ge=0)
    ok: bool
