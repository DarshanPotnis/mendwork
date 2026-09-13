"""Facts about a page element, read without reading any field's content.

Recording builds fingerprints from them, and healing compares live elements with a
fingerprint through them, so both read exactly the same facts.
"""

from mendwork.engine.domain.base import DomainModel


class Box(DomainModel):
    """An element's position as fractions of the whole document."""

    x: float
    y: float
    width: float
    height: float


class ElementFacts(DomainModel):
    """What the page says about an element, read without reading any field's content."""

    tag: str
    id: str | None = None
    name: str | None = None
    type: str | None = None
    autocomplete: str | None = None
    placeholder: str | None = None
    aria_label: str | None = None
    data_testid: str | None = None
    href: str | None = None
    """The link's path, resolved against the page."""
    label_text: str | None = None
    text: str | None = None
    """Rendered text; absent for fields and for anything that holds typed or masked text."""
    own_text: str | None = None
    """The element's direct text, without its children's text."""
    nearby_text: tuple[str, ...] = ()
    structural_path: str
    box: Box | None = None
    text_entry: bool = False
    masked: bool = False
    """A password input, a credential autocomplete, or text masked by CSS."""
    in_form: bool = False
    form_submit: bool = False
    """Activating it submits its form."""
    form_has_password: bool = False
