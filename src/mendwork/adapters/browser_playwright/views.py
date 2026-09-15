"""Clipping a screenshot around one element: a viewport-sized window, kept inside the document.

A run report outlines a healed element on a screenshot taken just before its action. The window is
centred on the element and moved back inside the document where it would leave it, so the element
is in the picture wherever it sits on a long page, and the page is never scrolled to take it.
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from mendwork.engine.ports.element_types import Box


class ElementGeometry(BaseModel):
    """What element_view.js reports, in CSS pixels."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    x: float
    y: float
    width: float = Field(ge=0.0)
    height: float = Field(ge=0.0)
    document_width: float = Field(alias="documentWidth", ge=0.0)
    document_height: float = Field(alias="documentHeight", ge=0.0)
    viewport_width: float = Field(alias="viewportWidth", ge=0.0)
    viewport_height: float = Field(alias="viewportHeight", ge=0.0)
    connected: bool


@dataclass(frozen=True, slots=True)
class Clip:
    """A rectangle of the page, in CSS pixels from its top left corner."""

    x: float
    y: float
    width: float
    height: float


def view_clip(geometry: ElementGeometry) -> tuple[Clip, Box]:
    """The window to capture around the element, and the element's visible part in it.

    The window is as large as the viewport, or the document where the document is smaller, and
    never less than one pixel. The box is the part of the element inside the window, as fractions
    of the window.
    """
    width = max(1.0, min(geometry.viewport_width, geometry.document_width))
    height = max(1.0, min(geometry.viewport_height, geometry.document_height))
    left = _within(geometry.x + geometry.width / 2 - width / 2, geometry.document_width - width)
    top = _within(geometry.y + geometry.height / 2 - height / 2, geometry.document_height - height)
    right = min(geometry.x + geometry.width, left + width)
    bottom = min(geometry.y + geometry.height, top + height)
    visible_left = max(geometry.x, left)
    visible_top = max(geometry.y, top)
    box = Box(
        x=_fraction((visible_left - left) / width),
        y=_fraction((visible_top - top) / height),
        width=_fraction(max(0.0, right - visible_left) / width),
        height=_fraction(max(0.0, bottom - visible_top) / height),
    )
    return Clip(x=left, y=top, width=width, height=height), box


def _within(value: float, largest: float) -> float:
    return max(0.0, min(value, max(0.0, largest)))


def _fraction(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)
