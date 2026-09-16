"""The scorecard's HTML: self-contained, escaped, and drawn to the mark spec (ADR 0014)."""

import re
from html import unescape

import pytest

from mendwork.adapters.report_html.markup import element
from mendwork.adapters.scorecard_html.charts import BAR, LABEL_WIDTH, bar_chart, bar_path
from mendwork.adapters.scorecard_html.render import CONTENT_SECURITY_POLICY
from mendwork.adapters.scorecard_html.writer import load_css, scorecard_document
from mendwork.engine.benchmark.results import BenchmarkResults, SystemDescription, SystemKind
from mendwork.engine.reporting.scorecard_view import Bar, BarChart
from tests.unit.reporting.test_scorecard_view import grid_document

HOSTILE = '<img src=x onerror="alert(1)">'


def hostile_document() -> BenchmarkResults:
    document = grid_document()
    systems = tuple(
        system.model_copy(update={"description": HOSTILE}) if system.id == "ladder_free" else system
        for system in document.systems
    )
    return document.model_copy(update={"systems": systems})


def test_the_scorecard_loads_nothing_and_runs_nothing() -> None:
    html = scorecard_document([grid_document()])

    policies = re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', html)
    assert [unescape(policy) for policy in policies] == [CONTENT_SECURITY_POLICY]
    lowered = html.lower()
    for forbidden in ("<script", " src=", "href=", "url(", "@import", "<link", "<img", "<iframe"):
        assert forbidden not in lowered, forbidden
    assert html.startswith('<!doctype html><html lang="en">')


def test_text_from_a_results_file_is_escaped_wherever_it_appears() -> None:
    html = scorecard_document([hostile_document()])

    assert HOSTILE not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


def test_every_chart_is_an_inline_svg_with_a_title_on_every_mark() -> None:
    html = scorecard_document([grid_document()])

    charts = re.findall(r"<svg [^>]*>", html)
    assert len(charts) == 5
    assert all('viewBox="0 0 700 ' in chart and 'role="img"' in chart for chart in charts)
    # Four comparison charts of three bars; the stack has two, three, and one non-empty segments.
    assert html.count('<g class="mark ') == 12
    assert html.count('<g class="segment ') == 6
    assert "Mendwork, free rungs: 0 of 4 (0.0%)" in html


def test_a_bar_is_square_at_its_baseline_rounded_at_its_end_and_absent_at_zero() -> None:
    rounded = bar_path(190, 10, 120, BAR)
    square = bar_path(190, 10, 120, BAR, rounded=False)

    assert bar_path(190, 10, 0, BAR) == ""
    assert rounded.startswith("M190.00 10.00H306.00Q310.00 10.00 310.00 14.00")
    assert rounded.endswith("H190.00Z")
    assert "Q" not in square
    assert bar_path(190, 10, 2, BAR).startswith("M190.00 10.00H190.00Q192.00")


def test_a_zero_bar_draws_no_mark_but_keeps_its_hit_area_and_value() -> None:
    chart = BarChart("c", "Title", "Note", (Bar("System", True, 0.0, "0 of 4 (0.0%)"),))

    svg = bar_chart(chart).html

    assert 'class="bar"' not in svg
    assert f'<rect class="hit" x="{LABEL_WIDTH}"' in svg
    assert ">0 of 4 (0.0%)</text>" in svg


def test_the_stylesheet_ships_as_package_data_with_both_themes() -> None:
    css = load_css()

    assert "--wrong: #e34948" in css
    assert "prefers-color-scheme: dark" in css


def test_svg_attribute_names_may_be_camel_cased_but_tags_may_not() -> None:
    assert element("svg", {"viewBox": "0 0 1 1"}).html == '<svg viewBox="0 0 1 1"></svg>'
    with pytest.raises(ValueError, match="plain tag"):
        element("Svg")
    with pytest.raises(ValueError, match="plain tag"):
        element("svg", {'view"Box': "x"})


def test_a_system_description_is_what_the_page_names_a_system() -> None:
    description = SystemDescription(
        id="role_name",
        label="Role + name locator",
        short_label="Role + name script",
        kind=SystemKind.SCRIPT,
        description="d",
    )

    assert description.short_label == "Role + name script"
