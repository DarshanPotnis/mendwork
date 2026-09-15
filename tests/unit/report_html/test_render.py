"""The run report file: self-contained, escaped, budgeted, and free of secrets (ADR 0013)."""

import base64
import re
from dataclasses import replace
from html.parser import HTMLParser
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr

from mendwork.adapters.report_html.markup import element, text
from mendwork.adapters.report_html.render import CONTENT_SECURITY_POLICY, render_report
from mendwork.adapters.report_html.writer import ReportWriter, load_css
from mendwork.engine.domain.runs import ArtifactName, Run, RunId
from mendwork.engine.reporting.view import RunReportView, run_report_view
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.png import tiny_png
from tests.secret_search import leaks
from tests.unit.patching.builders import ledger, ledger_page, ledger_version

FOUND: Final = ArtifactName("steps/002_export.found.png")
AFTER: Final = ArtifactName("steps/002_export.png")
OPENED: Final = ArtifactName("steps/001_open.png")
FORBIDDEN_TAGS: Final = frozenset(
    {"script", "link", "iframe", "object", "embed", "base", "form", "audio", "video", "source"}
)
URL_ATTRIBUTES: Final = frozenset(
    {"src", "href", "srcset", "action", "poster", "data", "formaction"}
)


class Parsed(HTMLParser):
    """Every tag, attribute, and piece of text in a document."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str]] = []
        self.texts: list[str] = []
        self.style = ""
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend((name, value or "") for name, value in attrs)
        self._in_style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.style += data
        else:
            self.texts.append(data)


def parsed(document: str) -> Parsed:
    parser = Parsed()
    parser.feed(document)
    parser.close()
    return parser


async def ledger_view() -> tuple[Run, RunReportView]:
    made = await ledger()
    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher())
    return run, run_report_view(run, ledger_version())


def render(view: RunReportView, images: dict[ArtifactName, bytes], budget: int = 1 << 20) -> str:
    return render_report(view, images, css=load_css(), budget_bytes=budget, scrub=lambda t: t)


@pytest.mark.asyncio
async def test_the_report_can_load_nothing_from_anywhere() -> None:
    _, view = await ledger_view()
    images = {FOUND: tiny_png(), AFTER: tiny_png(shade=10), OPENED: tiny_png(shade=200)}

    document = parsed(render(view, images))

    assert FORBIDDEN_TAGS.isdisjoint(document.tags)
    for name, value in document.attributes:
        if name in URL_ATTRIBUTES:
            assert value.startswith(("data:image/png;base64,", "#step-")), (name, value)
    assert ("content", CONTENT_SECURITY_POLICY) in document.attributes
    assert re.search(r"url\(|@import", document.style) is None
    assert document.tags.count("img") == 3


@pytest.mark.asyncio
async def test_screenshots_are_embedded_and_the_healed_element_is_outlined_in_place() -> None:
    _, view = await ledger_view()
    found = tiny_png(shade=77)

    document = render(view, {FOUND: found, AFTER: tiny_png(), OPENED: tiny_png()})

    assert f"data:image/png;base64,{base64.b64encode(found).decode()}" in document
    assert 'class="mark" style="left:40.00%;top:30.00%;width:10.00%;height:5.00%"' in document
    assert "Recorded: a button named &quot;Export ledger&quot;." in document


@pytest.mark.asyncio
async def test_the_budget_embeds_the_most_telling_screenshots_first_and_names_the_rest() -> None:
    _, view = await ledger_view()
    image = tiny_png()

    document = render(view, {FOUND: image, AFTER: image, OPENED: b"not a png"}, budget=len(image))

    assert document.count("data:image/png;base64,") == 1
    assert (
        f"Screenshot {AFTER} is not embedded: the report&#x27;s screenshot budget is used up"
        in (document)
    )
    assert f"Screenshot {OPENED} is not embedded: it is not a PNG image" in document


@pytest.mark.asyncio
async def test_a_missing_screenshot_is_named_rather_than_broken() -> None:
    _, view = await ledger_view()

    document = render(view, {})

    assert "<img" not in document
    assert f"Screenshot {FOUND} is not embedded: the file is missing" in document


@pytest.mark.asyncio
async def test_text_is_scrubbed_of_secrets_after_rendering_and_images_stay_whole() -> None:
    run, view = await ledger_view()
    secret = "Zq7-leak/probe &4421 ü"
    leaky = replace(view, steps=(view.steps[0], replace(view.steps[1], error=f"typed {secret}")))
    scrubber = SecretScrubber()
    scrubber.register(SecretStr(secret))
    image = tiny_png()

    document = render_report(
        leaky, {FOUND: image}, css=load_css(), budget_bytes=1 << 20, scrub=scrubber.scrub_text
    )

    assert leaks(document.encode("utf-8"), secret) == []
    assert base64.b64encode(image).decode() in document
    assert run.run_id in document


@given(st.text(alphabet=st.characters(exclude_characters="\r"), max_size=200))
def test_no_text_can_become_markup(value: str) -> None:
    # HTML parsers turn a carriage return into a line feed, so the property is stated without one.
    document = parsed(element("p", {"title": value}, value).html)

    assert document.tags == ["p"]
    assert document.attributes == [("title", value)]
    assert "".join(document.texts) == value


def test_tag_and_attribute_names_must_be_plain_words() -> None:
    with pytest.raises(ValueError, match="not a plain tag"):
        element("img onerror=x", None)
    with pytest.raises(ValueError, match="not a plain tag"):
        element("p", {"on click": "x"})
    assert text("<b>").html == "&lt;b&gt;"


@pytest.mark.asyncio
async def test_the_writer_reads_the_run_s_screenshots_and_stores_the_report() -> None:
    made = await ledger()
    run = await made.run(ledger_page(), ledger_version(), patcher=made.patcher())
    images = {FOUND: tiny_png()}

    def read(run_id: RunId, name: ArtifactName) -> bytes | None:
        assert run_id == run.run_id
        return images.get(name)

    writer = ReportWriter(
        artifacts=made.artifacts, read=read, scrubber=SecretScrubber(), budget_bytes=1 << 20
    )

    name = await writer.write(run, ledger_version())

    assert name == ArtifactName("report.html")
    stored = made.artifacts.files[(run.run_id, name)].decode("utf-8")
    assert stored.startswith("<!doctype html>")
    assert stored.count("data:image/png;base64,") == 1
