"""Hypothesis strategies that generate valid workflow versions, tricky text included.

Generated workflows declare exactly the inputs and secrets their steps use, and never fill
a credential-looking field with anything but a secret, so every example is valid by
construction rather than filtered into validity.
"""

from datetime import UTC, datetime
from typing import Any, Final

from hypothesis import strategies as st

from mendwork.engine.domain.enums import AriaRole
from mendwork.engine.domain.workflow import WorkflowVersion

# Strings a YAML emitter or reader could mistake for another type or for syntax.
TRICKY_TEXT: Final = (
    "yes", "No", "off", "null", "~", "true", "FALSE", "2026-01-01", "2026-09-11T08:30:00Z",
    "12:30", "1e5", "0o17", "012", "0x1F", "1_000", ".inf", "-.5", "+1", "- item", "key: value",
    "#comment", "a # b", "'quoted'", '"double"', "back\\slash", " leading", "trailing ", "@at",
    "`tick", "%percent", "!tag", "&anchor", "*alias", "|", "> folded", "{flow}", "[flow]",
    "? question", "::", "ü", "🚀", "a‍b", "right‏to‎left", "\u00a0nbsp",
)  # fmt: skip

_TEXT_ALPHABET: Final = st.characters(codec="utf-8", exclude_categories=("Cc", "Cs", "Zl", "Zp"))
_SAFE_CSS: Final = (
    "#save",
    ".btn > span",
    "button[type=submit]",
    "tr:nth-child(2) td",
    "a[href^='/orders']",
)
_REGEXES: Final = (r"https?://[^?#]+/dashboard\.html(?:[?#].*)?", r"report_\d{4}\.csv", r"(?i)done")


def text(max_size: int = 40) -> st.SearchStrategy[str]:
    generated = st.text(_TEXT_ALPHABET, min_size=1, max_size=max_size).filter(str.strip)
    return st.one_of(st.sampled_from(TRICKY_TEXT), generated)


def literal_text() -> st.SearchStrategy[str]:
    return st.one_of(text(), st.just(""), st.just("line one\nline two\tend"))


def slugs() -> st.SearchStrategy[str]:
    return st.from_regex(r"[a-z][a-z0-9]{0,6}(?:_[a-z0-9]{1,4}){0,2}", fullmatch=True)


def _optional(strategy: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    return st.one_of(st.none(), strategy)


def _without_none(**fields: object) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


@st.composite
def selectors(draw: st.DrawFn, depth: int = 2) -> dict[str, Any]:
    strategy = draw(
        st.sampled_from(("test_id", "role_name", "label", "placeholder", "text", "css"))
    )
    selector: dict[str, Any] = {"strategy": strategy}
    if strategy == "role_name":
        selector |= {"role": draw(st.sampled_from(list(AriaRole))).value, "name": draw(text())}
    elif strategy == "css":
        selector["value"] = draw(st.sampled_from(_SAFE_CSS))
    else:
        selector["value"] = draw(text())
    if strategy not in {"test_id", "css"}:
        selector["exact"] = draw(st.booleans())
    if depth and draw(st.booleans()):
        selector["within"] = draw(selectors(depth=depth - 1))
    return selector


@st.composite
def bboxes(draw: st.DrawFn) -> dict[str, float]:
    unit = st.floats(0.0, 1.0, allow_nan=False, allow_infinity=False)
    x, y = draw(unit), draw(unit)
    width = draw(st.floats(0.0, 1.0 - x, allow_nan=False))
    height = draw(st.floats(0.0, 1.0 - y, allow_nan=False))
    return {"x": x, "y": y, "width": width, "height": height}


@st.composite
def fingerprints(draw: st.DrawFn, *, input_type: str | None = None) -> dict[str, Any]:
    attributes = _without_none(
        id=draw(_optional(text())),
        name=draw(_optional(text())),
        type=input_type or draw(_optional(st.sampled_from(("button", "submit", "checkbox")))),
        placeholder=draw(_optional(text())),
        aria_label=draw(_optional(text())),
        data_testid=draw(_optional(text())),
        href=draw(_optional(st.sampled_from(("/", "/orders.html", "/a b/ü")))),
    )
    unique_selectors = st.lists(selectors(), min_size=1, max_size=3, unique_by=repr)
    return _without_none(
        tag=draw(st.sampled_from(("button", "input", "a", "my-widget"))),
        role=draw(_optional(st.sampled_from(list(AriaRole)))),
        accessible_name=draw(_optional(text())),
        text=draw(_optional(text(80))),
        label_text=draw(_optional(text())),
        attributes=attributes or None,
        nearby_text=draw(st.lists(text(), max_size=3)) or None,
        structural_path=draw(text(60)),
        bbox=draw(_optional(bboxes())),
        selectors=draw(unique_selectors),
    )


def _timeout() -> st.SearchStrategy[int | None]:
    return _optional(st.integers(1, 600_000))


@st.composite
def checkpoints(draw: st.DrawFn) -> dict[str, Any]:
    kind = draw(
        st.sampled_from(
            (
                "url_matches",
                "element_visible",
                "text_present",
                "download_completed",
                "response_received",
                "no_error_banner",
            )
        )
    )
    timeout = draw(_timeout())
    match kind:
        case "url_matches" | "response_received":
            mode = draw(st.sampled_from(("exact", "prefix", "regex")))
            pattern = (
                draw(st.sampled_from(_REGEXES))
                if mode == "regex"
                else "https://portal.example.test/app/"
            )
            checkpoint: dict[str, Any] = {"kind": kind, "mode": mode, "pattern": pattern}
            if kind == "response_received":
                low = draw(st.integers(100, 599))
                checkpoint |= {"status_min": low, "status_max": draw(st.integers(low, 599))}
        case "element_visible":
            checkpoint = {"kind": kind, "selector": draw(selectors())}
        case "text_present":
            checkpoint = {"kind": kind, "text": draw(text())}
        case "download_completed":
            checkpoint = {"kind": kind, "filename_pattern": draw(st.sampled_from(_REGEXES))}
        case _:
            return _without_none(kind=kind, selector=draw(_optional(selectors())))
    return _without_none(**checkpoint, timeout_ms=timeout)


@st.composite
def steps(draw: st.DrawFn, step_id: str) -> tuple[dict[str, Any], tuple[str, str] | None]:
    """A step and the (namespace, name) of the declaration it references, if any."""
    action = draw(st.sampled_from(("navigate", "click", "fill", "select", "press")))
    base: dict[str, Any] = {
        "id": step_id,
        "intent": draw(text()),
        "action": action,
        "risk": draw(st.sampled_from(("safe", "caution", "irreversible"))),
    }
    reference: tuple[str, str] | None = None
    match action:
        case "navigate":
            if draw(st.booleans()):
                url = draw(
                    st.sampled_from(
                        ("https://portal.example.test/ü", "http://127.0.0.1:8765/?a=1#b")
                    )
                )
                base["value"] = {"kind": "literal", "value": url}
            else:
                reference = ("url_input", f"url_{draw(slugs())}")
                base["value"] = {"kind": "input", "name": reference[1]}
        case "click":
            base["target"] = draw(fingerprints())
        case "press":
            base["key"] = draw(st.sampled_from(("Enter", "Tab", "Control+a", "Shift+Tab", "x")))
            if draw(st.booleans()):
                base["target"] = draw(fingerprints())
        case "fill" | "select":
            source = draw(
                st.sampled_from(
                    ("literal", "input", "secret") if action == "fill" else ("literal", "input")
                )
            )
            if source == "secret":
                reference = ("secret", f"secret_{draw(slugs())}")
                base["target"] = draw(fingerprints(input_type="password"))
                base["value"] = {"kind": "secret", "name": reference[1]}
            else:
                # An email-typed field is never credential-like, whatever its random labels say.
                base["target"] = draw(fingerprints(input_type="email"))
                if source == "literal":
                    base["value"] = {"kind": "literal", "value": draw(literal_text())}
                else:
                    reference = ("input", f"in_{draw(slugs())}")
                    base["value"] = {"kind": "input", "name": reference[1]}
    found = draw(st.lists(checkpoints(), max_size=2, unique_by=repr))
    if found:
        base["checkpoints"] = found
    return base, reference


@st.composite
def _declaration(draw: st.DrawFn, name: str, kind: str) -> dict[str, Any]:
    declaration: dict[str, Any] = {"name": name, "kind": kind}
    if draw(st.booleans()):
        default = {
            "text": draw(literal_text()),
            "date": "2026-02-10",
            "url": "https://portal.example.test/",
        }[kind]
        declaration |= {"required": False, "default": default}
    if draw(st.booleans()):
        declaration["description"] = draw(text())
    return declaration


@st.composite
def workflow_versions(draw: st.DrawFn) -> WorkflowVersion:
    ids = draw(st.lists(slugs(), min_size=1, max_size=4, unique=True))
    drawn = [draw(steps(step_id)) for step_id in ids]
    inputs: dict[str, dict[str, Any]] = {}
    secrets: list[str] = []
    for _, reference in drawn:
        if reference is None:
            continue
        namespace, name = reference
        if namespace == "secret":
            if name not in secrets:
                secrets.append(name)
        elif name not in inputs:
            kind = (
                "url"
                if namespace == "url_input"
                else draw(st.sampled_from(("text", "date", "url")))
            )
            inputs[name] = draw(_declaration(name, kind))

    number = draw(st.integers(1, 30))
    lineage: dict[str, Any] = {}
    if number > 1:
        change: dict[str, Any] = {"kind": "manual_edit", "summary": draw(text())}
        if number > 2 and draw(st.booleans()):
            change = {
                "kind": "rollback",
                "restored_version": draw(st.integers(1, number - 2)),
                "reason": draw(text()),
            }
        lineage = {"parent_version": number - 1, "change": change}

    # Hypothesis takes naive bounds and attaches the timezone itself.
    bounds = (datetime(2000, 1, 1), datetime(2100, 1, 1))  # noqa: DTZ001
    created = draw(st.datetimes(*bounds, timezones=st.just(UTC)))
    return WorkflowVersion.model_validate(
        {
            "schema_version": 1,
            "workflow_id": draw(slugs()),
            "version": number,
            "created_at": created,
            **lineage,
            "inputs": list(inputs.values()),
            "secrets": secrets,
            "steps": [step for step, _ in drawn],
        }
    )
