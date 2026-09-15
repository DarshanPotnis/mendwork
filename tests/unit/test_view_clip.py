"""The window an element screenshot is clipped to, and the element's box in it (ADR 0013)."""

import pytest

from mendwork.adapters.browser_playwright.views import Clip, ElementGeometry, view_clip
from mendwork.engine.ports.element_types import Box


def geometry(
    box: tuple[float, float, float, float],
    *,
    document: tuple[float, float] = (800.0, 3000.0),
    viewport: tuple[float, float] = (800.0, 600.0),
) -> ElementGeometry:
    x, y, width, height = box
    return ElementGeometry(
        x=x,
        y=y,
        width=width,
        height=height,
        document_width=document[0],
        document_height=document[1],
        viewport_width=viewport[0],
        viewport_height=viewport[1],
        connected=True,
    )


@pytest.mark.parametrize(
    ("measured", "clip", "box"),
    [
        pytest.param(
            geometry((300.0, 2800.0, 160.0, 40.0)),
            Clip(0.0, 2400.0, 800.0, 600.0),
            Box(x=0.375, y=0.6667, width=0.2, height=0.0667),
            id="low on a long page: the window stops at the document's end",
        ),
        pytest.param(
            geometry((1000.0, 1500.0, 100.0, 50.0), document=(2000.0, 3000.0)),
            Clip(650.0, 1225.0, 800.0, 600.0),
            Box(x=0.4375, y=0.4583, width=0.125, height=0.0833),
            id="in the middle: the window is centred on the element",
        ),
        pytest.param(
            geometry((10.0, 10.0, 50.0, 20.0), document=(500.0, 400.0)),
            Clip(0.0, 0.0, 500.0, 400.0),
            Box(x=0.02, y=0.025, width=0.1, height=0.05),
            id="a document smaller than the viewport: the window is the document",
        ),
        pytest.param(
            geometry((0.0, 100.0, 800.0, 2000.0)),
            Clip(0.0, 800.0, 800.0, 600.0),
            Box(x=0.0, y=0.0, width=1.0, height=1.0),
            id="taller than the window: only its visible part is outlined",
        ),
        pytest.param(
            geometry((0.0, 0.0, 0.0, 0.0), document=(0.0, 0.0)),
            Clip(0.0, 0.0, 1.0, 1.0),
            Box(x=0.0, y=0.0, width=0.0, height=0.0),
            id="an empty document: a one-pixel window",
        ),
    ],
)
def test_the_window_holds_the_element_and_stays_inside_the_document(
    measured: ElementGeometry, clip: Clip, box: Box
) -> None:
    assert view_clip(measured) == (clip, box)
