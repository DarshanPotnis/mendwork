"""Element facts read in the page: the one parser for element_facts.js, for replay and recording.

Healing compares live elements with a recorded fingerprint through exactly the facts the
recorder built that fingerprint from, so both read them through this module.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from mendwork.engine.domain.limits import NEARBY_TEXT_MAX_ITEMS
from mendwork.engine.ports.element_types import Box, ElementFacts

STRUCTURAL_PATH_MAX_LEVELS: Final = 12
FACTS_REQUEST: Final = {"nearbyMax": NEARBY_TEXT_MAX_ITEMS, "pathMax": STRUCTURAL_PATH_MAX_LEVELS}
"""The argument element_facts.js is evaluated with."""


class _Reply(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class BoxReply(_Reply):
    """An element's box as fractions of the document."""

    x: float
    y: float
    width: float
    height: float


class FactsReply(_Reply):
    """What element_facts.js returns. No field could carry a field's content."""

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
