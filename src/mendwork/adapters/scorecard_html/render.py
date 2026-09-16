"""The benchmark scorecard as one self-contained HTML file (ADR 0014).

Like the run report, nothing on the page loads anything: the stylesheet is inlined, the charts are
inline SVG, fonts are the reader's own, there is no script and no link, and a
Content-Security-Policy refuses every fetch, so even a mistake here could not reach the network.
"""

from typing import Final

from mendwork.adapters.report_html.markup import Markup, element, join
from mendwork.adapters.scorecard_html.charts import bar_chart, stack_chart
from mendwork.engine.reporting.scorecard_view import (
    ProvenanceView,
    ScorecardView,
    SectionView,
    Table,
    Tile,
)

CONTENT_SECURITY_POLICY: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
)
LEDE: Final = (
    "Workflows replayed through plain Playwright scripts and through Mendwork on the same page "
    "states, with every action checked against ground truth when it was sent."
)


def render_scorecard(view: ScorecardView, *, css: str) -> str:
    """The scorecard document."""
    body = element(
        "main",
        None,
        element(
            "header",
            None,
            element("h1", None, view.title),
            element("p", {"class": "lede"}, LEDE),
            element("div", {"class": "tiles"}, join(_tile(tile) for tile in view.tiles)),
        ),
        join(_section(section) for section in view.sections),
        join(
            element(
                "section",
                {"class": "skipped"},
                element("h2", None, title),
                element("p", None, f"Not run: {reason}"),
            )
            for title, reason in view.skipped
        ),
        element(
            "section",
            None,
            element("h2", None, "Systems"),
            element(
                "dl",
                {"class": "systems"},
                join(_fact(label, description) for label, description in view.systems),
            ),
        ),
        element(
            "section",
            None,
            element("h2", None, "How it was measured"),
            element("ul", None, join(element("li", None, line) for line in view.methodology)),
        ),
        join(_provenance(item) for item in view.provenance),
        element(
            "footer",
            None,
            "Written by Mendwork from benchmark results files. Nothing on this page loads from "
            "anywhere.",
        ),
    )
    return "<!doctype html>" + (
        element(
            "html",
            {"lang": "en"},
            element(
                "head",
                None,
                element("meta", {"charset": "utf-8"}),
                element(
                    "meta", {"name": "viewport", "content": "width=device-width, initial-scale=1"}
                ),
                element(
                    "meta",
                    {"http-equiv": "Content-Security-Policy", "content": CONTENT_SECURITY_POLICY},
                ),
                element("title", None, view.title),
                element("style", None, Markup(css)),
            ),
            element("body", None, body),
        ).html
    )


def _tile(tile: Tile) -> Markup:
    return element(
        "div",
        {"class": "tile"},
        element("p", {"class": "tile-label"}, tile.label),
        element("p", {"class": "tile-value"}, tile.value),
        element("p", {"class": "tile-detail"}, tile.detail),
    )


def _section(section: SectionView) -> Markup:
    return element(
        "section",
        {"id": section.id},
        element("h2", None, section.title),
        element("p", None, section.description),
        _legend((("ladder", "Mendwork"), ("script", "Script baseline")), "bar"),
        element(
            "div",
            {"class": "charts"},
            join(
                element(
                    "figure",
                    None,
                    element("h3", None, chart.title),
                    element("p", {"class": "note"}, chart.note),
                    element("div", {"class": "chart-scroll"}, bar_chart(chart)),
                )
                for chart in section.charts
            ),
        ),
        element(
            "figure",
            {"class": "wide"},
            element("h3", None, "Outcome of every reached step"),
            _legend(section.groups, "segment"),
            element(
                "div",
                {"class": "chart-scroll"},
                stack_chart(
                    f"{section.id}-outcomes", "Outcome of every reached step", section.stack
                ),
            ),
        ),
        join(_table(table) for table in section.tables),
        element("p", {"class": "digest"}, f"Outcomes digest: {section.digest}"),
    )


def _legend(items: tuple[tuple[str, str], ...], kind: str) -> Markup:
    return element(
        "ul",
        {"class": "legend"},
        join(
            element(
                "li",
                None,
                element("span", {"class": f"swatch {kind} {key}"}),
                element("span", None, label),
            )
            for key, label in items
        ),
    )


def _table(table: Table) -> Markup:
    return element(
        "div",
        {"class": "table-scroll"},
        element(
            "table",
            None,
            element("caption", None, table.caption),
            element(
                "thead",
                None,
                element(
                    "tr", None, join(element("th", {"scope": "col"}, cell) for cell in table.header)
                ),
            ),
            element(
                "tbody",
                None,
                join(
                    element(
                        "tr",
                        None,
                        element("th", {"scope": "row"}, row[0]),
                        join(element("td", None, cell) for cell in row[1:]),
                    )
                    for row in table.rows
                ),
            ),
        ),
    )


def _provenance(item: ProvenanceView) -> Markup:
    return element(
        "section",
        {"class": "provenance"},
        element("h2", None, f"Provenance: {item.title}"),
        element("dl", None, join(_fact(label, value) for label, value in item.facts)),
        element(
            "details",
            None,
            element("summary", None, "Source digests"),
            element("dl", None, join(_fact(name, digest) for name, digest in item.digests)),
        ),
    )


def _fact(label: str, value: str) -> Markup:
    return join((element("dt", None, label), element("dd", None, value)))
