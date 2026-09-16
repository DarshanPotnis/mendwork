"""Inline SVG charts for the scorecard (ADR 0014): no script, no reference, no image.

Every chart is drawn from the view model's numbers with ``markup.element``, so no page text can
become markup. Marks follow one spec: bars 16 units thick with a 4-unit rounded data end and a
square baseline, a 2-unit surface gap between stacked segments, hairline gridlines, and text in
text colours only. A page without script can offer only the browser's own tooltip, so every mark is
focusable and carries a ``<title>``; every value is also in the table beside its chart.
"""

from collections.abc import Sequence
from typing import Final

from mendwork.adapters.report_html.markup import Markup, element, join
from mendwork.engine.reporting.scorecard_view import BarChart, StackRow

WIDTH: Final = 700
LABEL_WIDTH: Final = 220
"""Wide enough for the longest label a results file may carry (28 characters, ADR 0014)."""
PLOT_WIDTH: Final = 320
ROW: Final = 34
BAR: Final = 16
TOP: Final = 8
AXIS: Final = 26
RADIUS: Final = 4.0
GAP: Final = 2.0
VALUE_GAP: Final = 8
TICKS: Final = (0.0, 0.25, 0.5, 0.75, 1.0)


def bar_path(x: float, y: float, width: float, height: float, *, rounded: bool = True) -> str:
    """A bar from ``x``, square at its baseline and rounded at its data end; empty at zero width."""
    if width <= 0:
        return ""
    right = x + width
    if not rounded:
        return f"M{x:.2f} {y:.2f}H{right:.2f}V{y + height:.2f}H{x:.2f}Z"
    radius = min(RADIUS, width, height / 2)
    return (
        f"M{x:.2f} {y:.2f}H{right - radius:.2f}"
        f"Q{right:.2f} {y:.2f} {right:.2f} {y + radius:.2f}"
        f"V{y + height - radius:.2f}"
        f"Q{right:.2f} {y + height:.2f} {right - radius:.2f} {y + height:.2f}"
        f"H{x:.2f}Z"
    )


def bar_chart(chart: BarChart) -> Markup:
    """One metric, one row per system, on a 0 to 100% scale."""
    rows = len(chart.bars)
    height = TOP + ROW * rows + AXIS
    marks = []
    for index, bar in enumerate(chart.bars):
        top = TOP + index * ROW
        y = top + (ROW - BAR) / 2
        width = PLOT_WIDTH * (bar.value or 0.0)
        centre = f"{top + ROW / 2:.2f}"
        marks.append(
            element(
                "g",
                {"class": "mark ladder" if bar.emphasis else "mark script", "tabindex": "0"},
                element("title", None, f"{bar.label}: {bar.text}"),
                element(
                    "rect",
                    {
                        "class": "hit",
                        "x": str(LABEL_WIDTH),
                        "y": str(top),
                        "width": str(PLOT_WIDTH),
                        "height": str(ROW),
                    },
                ),
                element("path", {"class": "bar", "d": bar_path(LABEL_WIDTH, y, width, BAR)})
                if width > 0
                else None,
                element("text", {"class": "label", "x": "0", "y": centre}, bar.label),
                element(
                    "text",
                    {"class": "value", "x": f"{LABEL_WIDTH + width + VALUE_GAP:.2f}", "y": centre},
                    bar.text,
                ),
            )
        )
    return _svg(chart.id, chart.title, chart.note, height, _grid(rows), join(marks))


def stack_chart(chart_id: str, title: str, rows: Sequence[StackRow]) -> Markup:
    """Each system's reached steps split into outcome groups, one row per system."""
    height = TOP + ROW * len(rows) + AXIS
    marks = []
    for index, row in enumerate(rows):
        top = TOP + index * ROW
        y = top + (ROW - BAR) / 2
        centre = f"{top + ROW / 2:.2f}"
        cursor = float(LABEL_WIDTH)
        segments = []
        for number, segment in enumerate(row.segments):
            last = number == len(row.segments) - 1
            width = PLOT_WIDTH * segment.share
            drawn = width if last else max(width - GAP, 0.0)
            segments.append(
                element(
                    "g",
                    {"class": f"segment {segment.group}", "tabindex": "0"},
                    element(
                        "title",
                        None,
                        f"{row.label}: {segment.label}, {segment.count:,} of {row.reached:,} "
                        f"steps ({segment.share:.1%})",
                    ),
                    element("path", {"d": bar_path(cursor, y, drawn, BAR, rounded=last)}),
                )
            )
            cursor += width
        marks.append(
            join(
                (
                    element("text", {"class": "label", "x": "0", "y": centre}, row.label),
                    *segments,
                    element(
                        "text",
                        {"class": "value", "x": f"{cursor + VALUE_GAP:.2f}", "y": centre},
                        f"{row.reached:,} steps",
                    ),
                )
            )
        )
    note = "Every reached step, split by outcome; the table below holds the counts."
    return _svg(chart_id, title, note, height, _grid(len(rows)), join(marks))


def _grid(rows: int) -> Markup:
    bottom = TOP + ROW * rows
    lines = []
    for tick in TICKS:
        x = f"{LABEL_WIDTH + PLOT_WIDTH * tick:.2f}"
        lines.append(
            element(
                "line",
                {
                    "class": "axis" if tick == 0 else "gridline",
                    "x1": x,
                    "x2": x,
                    "y1": str(TOP),
                    "y2": str(bottom),
                },
            )
        )
        lines.append(
            element("text", {"class": "tick", "x": x, "y": str(bottom + 18)}, f"{tick:.0%}")
        )
    return element("g", {"aria-hidden": "true"}, join(lines))


def _svg(chart_id: str, title: str, note: str, height: int, *children: Markup) -> Markup:
    return element(
        "svg",
        {
            "class": "chart",
            "viewBox": f"0 0 {WIDTH} {height}",
            "role": "img",
            "aria-labelledby": f"{chart_id}-title {chart_id}-note",
            "xmlns": "http://www.w3.org/2000/svg",
        },
        element("title", {"id": f"{chart_id}-title"}, title),
        element("desc", {"id": f"{chart_id}-note"}, note),
        *children,
    )
