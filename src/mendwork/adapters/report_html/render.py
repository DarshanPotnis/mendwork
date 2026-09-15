"""The run report as one self-contained HTML file (ADR 0013).

Nothing on the page loads anything: the stylesheet is inlined, screenshots are PNG data URIs, fonts
are the reader's own, there is no script, and a Content-Security-Policy refuses every fetch but
``data:`` images, so even a mistake in this file could not reach the network. Artifact paths are
shown as text, never as links.

Screenshots are embedded most telling first (found elements, the stopping step, healed steps, then
the rest) until the byte budget is used; any other is named instead. Text is scrubbed of the run's
secrets after it is rendered and before screenshots are put in, and screenshots were masked when
they were taken.
"""

import base64
from collections.abc import Callable, Mapping
from typing import Final

from mendwork.adapters.report_html.markup import Markup, element, join
from mendwork.engine.domain.patches import ImageBox
from mendwork.engine.domain.runs import ArtifactName
from mendwork.engine.patching.diff import TargetDiff
from mendwork.engine.reporting.view import FoundView, RunReportView, StepView

PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
CONTENT_SECURITY_POLICY: Final = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'"
)
_PLACEHOLDER: Final = "@@mendwork-image-{}@@"


def render_report(
    view: RunReportView,
    images: Mapping[ArtifactName, bytes],
    *,
    css: str,
    budget_bytes: int,
    scrub: Callable[[str], str],
) -> str:
    """The report document."""
    embedded, missing = _choose_images(view, images, budget_bytes)
    placeholders = {name: _PLACEHOLDER.format(number) for number, name in enumerate(embedded)}
    body = element(
        "main",
        None,
        _header(view),
        _timeline(view),
        join(_step(step, placeholders, missing) for step in view.steps),
        element(
            "footer",
            None,
            "Written by Mendwork from the run's record. Nothing on this page loads from anywhere.",
        ),
    )
    document = (
        "<!doctype html>"
        + element(
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
    scrubbed = scrub(document)
    for name, placeholder in placeholders.items():
        encoded = base64.b64encode(embedded[name]).decode("ascii")
        scrubbed = scrubbed.replace(placeholder, encoded)
    return scrubbed


def _choose_images(
    view: RunReportView, images: Mapping[ArtifactName, bytes], budget_bytes: int
) -> tuple[dict[ArtifactName, bytes], dict[ArtifactName, str]]:
    embedded: dict[ArtifactName, bytes] = {}
    missing: dict[ArtifactName, str] = {}
    remaining = budget_bytes
    for name in view.images():
        data = images.get(name)
        if data is None:
            missing[name] = "the file is missing"
        elif not data.startswith(PNG_SIGNATURE):
            missing[name] = "it is not a PNG image"
        elif len(data) > remaining:
            missing[name] = "the report's screenshot budget is used up"
        else:
            embedded[name] = data
            remaining -= len(data)
    return embedded, missing


def _header(view: RunReportView) -> Markup:
    facts = [
        ("Run", view.run_id),
        ("Started", view.started),
        ("Duration", view.duration),
        ("Steps", view.steps_line),
        ("Models", view.usage),
    ]
    if view.source is not None:
        facts.append(("Source", view.source))
    return element(
        "header",
        {"class": "run"},
        element("h1", None, f"{view.workflow_id} v{view.version}"),
        element("p", {"class": f"status {view.tone}"}, view.status),
        element("dl", None, join(_fact(label, value) for label, value in facts)),
        element("p", {"class": "error"}, view.error) if view.error else None,
        element("p", {"class": "warning"}, view.strength_summary)
        if view.strength_summary
        else None,
        _list("Patches", view.patches),
    )


def _timeline(view: RunReportView) -> Markup:
    rows = join(
        element(
            "li",
            {"class": step.tone},
            element("a", {"href": f"#step-{step.number}"}, f"{step.number}. {step.step_id}"),
            element(
                "span",
                {"class": "bar"},
                element(
                    "span", {"class": "fill", "style": f"width:{max(step.share, 0.02) * 100:.1f}%"}
                ),
            ),
            element("span", {"class": "time"}, step.duration),
            element("span", {"class": f"badge {step.strength.value}"}, step.strength.value),
            element("span", {"class": "result"}, step.status),
        )
        for step in view.steps
    )
    return element(
        "section",
        {"class": "timeline"},
        element("h2", None, "Timeline"),
        element("ol", None, rows),
    )


def _step(
    step: StepView, placeholders: Mapping[ArtifactName, str], missing: Mapping[ArtifactName, str]
) -> Markup:
    return element(
        "article",
        {"id": f"step-{step.number}", "class": f"step {step.tone}"},
        element("h2", None, f"Step {step.number} · {step.step_id} · {step.action}"),
        element("p", {"class": "intent"}, step.intent),
        element(
            "dl",
            None,
            _fact("Result", step.status),
            _fact("Duration", step.duration),
            _fact("Target", step.resolution),
            _fact("Checks", step.strength_words),
        ),
        _checks(step),
        element("p", {"class": "error"}, step.error) if step.error else None,
        _found(step.found, placeholders, missing) if step.found is not None else None,
        _screenshot(
            step.screenshot, None, f"The page as step {step.number} ended", placeholders, missing
        ),
        _ladder(step),
        _model(step),
        _list("Approval", [f"{item.proposal_id}: {item.decision}" for item in step.approvals]),
        _list("Outcome", [item.outcome for item in step.approvals if item.outcome is not None]),
        _list("Patch", step.patches),
        _list("Evidence kept beside the record", step.evidence),
    )


def _checks(step: StepView) -> Markup | None:
    if not step.checks:
        return None
    items = join(
        element(
            "li",
            {"class": "passed" if check.passed else "failed"},
            f"{'Passed' if check.passed else 'Failed'}: {check.words}",
            f" ({check.detail})" if check.detail else None,
        )
        for check in step.checks
    )
    return element("ul", {"class": "checks"}, items)


def _found(
    found: FoundView, placeholders: Mapping[ArtifactName, str], missing: Mapping[ArtifactName, str]
) -> Markup:
    caption = f"Recorded: {found.recorded}."
    if found.found is not None:
        caption += f" Found: {found.found}, outlined."
    return element(
        "section",
        {"class": "found"},
        element("h3", None, "The healed element"),
        _screenshot(found.screenshot, found.box, caption, placeholders, missing),
        element("p", {"class": "warning"}, found.problem) if found.problem else None,
        _target_table(found.diff) if found.diff is not None else None,
    )


def _screenshot(
    name: ArtifactName | None,
    box: ImageBox | None,
    caption: str,
    placeholders: Mapping[ArtifactName, str],
    missing: Mapping[ArtifactName, str],
) -> Markup | None:
    if name is None:
        return None
    placeholder = placeholders.get(name)
    if placeholder is None:
        reason = missing.get(name, "the file is missing")
        return element(
            "p", {"class": "missing"}, f"Screenshot {name} is not embedded: {reason}. {caption}"
        )
    mark = (
        element(
            "span",
            {
                "class": "mark",
                "style": (
                    f"left:{box.x * 100:.2f}%;top:{box.y * 100:.2f}%;"
                    f"width:{box.width * 100:.2f}%;height:{box.height * 100:.2f}%"
                ),
            },
        )
        if box is not None
        else None
    )
    return element(
        "figure",
        {"class": "shot"},
        element(
            "div",
            {"class": "frame"},
            element("img", {"alt": caption, "src": f"data:image/png;base64,{placeholder}"}),
            mark,
        ),
        element("figcaption", None, f"{caption} ({name})"),
    )


def _target_table(diff: TargetDiff) -> Markup:
    rows = [
        element(
            "tr",
            {"class": "changed" if field.changed else None},
            element("th", None, field.label),
            element("td", None, field.before or "none"),
            element("td", None, field.after or "none"),
        )
        for field in diff.fields
    ]
    rows.extend(
        element(
            "tr",
            {"class": "changed" if line.before != line.after else None},
            element("th", None, "Found"),
            element("td", None, line.before or "none"),
            element("td", None, line.after or "none"),
        )
        for line in diff.selectors
    )
    return element(
        "div",
        {"class": "table"},
        element(
            "table",
            None,
            element(
                "thead",
                None,
                element(
                    "tr",
                    None,
                    element("th", None, ""),
                    element("th", None, "Recorded"),
                    element("th", None, "Found"),
                ),
            ),
            element("tbody", None, join(rows)),
        ),
    )


def _ladder(step: StepView) -> Markup | None:
    if not step.rungs:
        return None
    items = join(
        element(
            "li",
            None,
            rung.heading,
            element("p", None, rung.numbers) if rung.numbers else None,
            element("ul", None, join(element("li", None, item) for item in rung.candidates))
            if rung.candidates
            else None,
        )
        for rung in step.rungs
    )
    return element(
        "details",
        None,
        element("summary", None, "The heal ladder, rung by rung"),
        element("ol", {"class": "rungs"}, items),
    )


def _model(step: StepView) -> Markup | None:
    model = step.model
    if model is None:
        return None
    return element(
        "section",
        {"class": "model"},
        element("h3", None, "What the model was shown"),
        element("ol", None, join(element("li", None, line) for line in model.shown)),
        element("p", None, model.answer),
        element("p", None, f"Its reason: {model.reason}") if model.reason else None,
        element("p", None, model.usage),
    )


def _fact(label: str, value: str) -> Markup:
    return join((element("dt", None, label), element("dd", None, value)))


def _list(title: str, items: tuple[str, ...] | list[str]) -> Markup | None:
    if not items:
        return None
    return element(
        "section",
        {"class": "notes"},
        element("h3", None, title),
        element("ul", None, join(element("li", None, item) for item in items)),
    )
