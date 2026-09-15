"""A run report loaded in Chromium: every request it makes, and where its outlines fall (ADR 0013).

The report is loaded twice: as written, and with its Content-Security-Policy removed, so a report
that loads nothing proves its markup references nothing, not only that the policy blocked it.
"""

import re
from dataclasses import dataclass
from typing import Final

from playwright.async_api import Browser, FloatRect, Page, Request, Route

_POLICY: Final = re.compile(r'<meta http-equiv="Content-Security-Policy"[^>]*>')
_TOLERANCE_PX: Final = 0.5


@dataclass(frozen=True, slots=True)
class LoadedReport:
    """What Chromium did with a report."""

    requests: tuple[str, ...]
    """Every URL either load asked for, ``data:`` URLs excepted."""
    policies: int
    """How many Content-Security-Policy elements the report carries."""
    images: int
    marks_inside: tuple[bool, ...]
    """For each outline, whether it lies inside its screenshot."""


async def load_report(browser: Browser, html: str) -> LoadedReport:
    """Load the report as written and without its policy; nothing it asks for ever leaves."""
    requests: list[str] = []
    policies = len(_POLICY.findall(html))
    images = 0
    marks: list[bool] = []
    for document in (html, _POLICY.sub("", html)):
        context = await browser.new_context()
        try:
            await context.route("**/*", _refuse)
            page = await context.new_page()
            page.on("request", lambda request: _note(requests, request))
            await page.set_content(document)
            if document is html:
                images = await page.locator("img").count()
                marks = await _marks_inside(page)
        finally:
            await context.close()
    return LoadedReport(tuple(requests), policies, images, tuple(marks))


async def _refuse(route: Route) -> None:
    await route.abort()


def _note(requests: list[str], request: Request) -> None:
    if not request.url.startswith("data:"):
        requests.append(request.url)


async def _marks_inside(page: Page) -> list[bool]:
    inside: list[bool] = []
    for frame in await page.locator("figure.shot .frame").all():
        image = await frame.locator("img").bounding_box()
        for mark in await frame.locator(".mark").all():
            box = await mark.bounding_box()
            inside.append(image is not None and box is not None and _within(box, image))
    return inside


def _within(box: FloatRect, image: FloatRect) -> bool:
    return (
        box["x"] >= image["x"] - _TOLERANCE_PX
        and box["y"] >= image["y"] - _TOLERANCE_PX
        and box["x"] + box["width"] <= image["x"] + image["width"] + _TOLERANCE_PX
        and box["y"] + box["height"] <= image["y"] + image["height"] + _TOLERANCE_PX
    )
