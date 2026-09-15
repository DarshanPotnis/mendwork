"""Static guards on product code: no chaos ground truth, and page scripts that follow the rules."""

import re
from pathlib import Path
from typing import Final

from mendwork.adapters.browser_playwright.scripts import PageScripts

REPO: Final = Path(__file__).resolve().parents[2]
PRODUCT: Final = REPO / "src" / "mendwork"
PAGE_SCRIPTS: Final = PRODUCT / "adapters" / "browser_playwright" / "js"
_WINDOW_PROPERTY: Final = re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)")
# A page script may touch exactly one global of its own, and read the window's computed style.
ALLOWED_WINDOW_PROPERTIES: Final = frozenset({"__mendwork", "getComputedStyle"})
# The recorder also listens on the window, checks whether it runs in a frame, and captures
# and deletes the binding Playwright installs for it.
RECORDER_WINDOW_PROPERTIES: Final = ALLOWED_WINDOW_PROPERTIES | {
    "__mendwork_recorder_binding",
    "addEventListener",
    "top",
}
# The recorder never reads what a person typed; the one read lives in field_text.js.
_FIELD_CONTENT: Final = re.compile(
    r"\.value\b|\bselectedOptions\b|\binnerText\b|\btextContent\b|\bFormData\b"
)
_NAMESPACE_BLOCK: Final = re.compile(
    r"// mendwork-namespace:begin\n.*?// mendwork-namespace:end\n", re.DOTALL
)
_MASK_BLOCK: Final = re.compile(r"// mendwork-mask:begin\n.*?// mendwork-mask:end\n", re.DOTALL)


def _product_files() -> list[Path]:
    return sorted(
        path
        for path in PRODUCT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )


def _source(name: str) -> str:
    return (PAGE_SCRIPTS / name).read_text(encoding="utf-8")


def test_no_product_file_refers_to_the_chaos_ground_truth() -> None:
    files = _product_files()
    assert len(files) > 80, "the guard found too few files to mean anything"

    offenders = [
        str(path.relative_to(REPO))
        for path in files
        if "__chaos" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


PORTAL: Final = REPO / "chaos-portal"
EXAMPLES: Final = REPO / "workflows" / "examples"
ENGINE: Final = PRODUCT / "engine"


# Words every sign-in form uses. They name a concept (the session vocabulary in Settings, the
# recorder's input descriptions), not one of the portal's controls.
UNIVERSAL_WEB_TERMS: Final = frozenset(
    {"Email address", "Log in", "Log out", "Sign in", "Sign out", "sign-in", "sign-out"}
)


def _portal_strings() -> set[str]:
    """The chaos portal's control labels, ids, and test ids that a healer could memorize.

    Single generic words ("Open", "Password") are left out: they belong to every web page.
    """
    targets = (PORTAL / "js" / "app" / "page-targets.js").read_text(encoding="utf-8")
    found: set[str] = set()
    for block in re.findall(r"synonyms: \[([^\]]*)\]", targets):
        found.update(re.findall(r'"([^"]+)"', block))
    found.update(re.findall(r'dangerousLabel: "([^"]+)"', targets))
    for page in PORTAL.glob("*.html"):
        found.update(
            re.findall(
                r'(?:id|data-testid)="([a-z]+-[a-z0-9-]+)"', page.read_text(encoding="utf-8")
            )
        )
    for example in EXAMPLES.glob("*.yaml"):
        found.update(
            match.strip()
            for match in re.findall(
                r"accessible_name: ([^#\n]+)", example.read_text(encoding="utf-8")
            )
        )
    return {text for text in found if " " in text or "-" in text} - UNIVERSAL_WEB_TERMS


def test_no_engine_file_names_the_chaos_portals_controls() -> None:
    labels = _portal_strings()
    assert len(labels) > 40, "the guard found too few portal strings to mean anything"
    files = sorted(ENGINE.rglob("*.py"))

    offenders = {
        str(path.relative_to(REPO)): sorted(
            label
            for label in labels
            if label.casefold() in path.read_text(encoding="utf-8").casefold()
        )
        for path in files
    }

    assert {name: found for name, found in offenders.items() if found} == {}


def test_every_page_script_is_type_checked_self_contained_and_namespaced() -> None:
    scripts = sorted(PAGE_SCRIPTS.glob("*.js"))
    assert [path.name for path in scripts] == [
        "element_ancestors.js",
        "element_facts.js",
        "element_identity.js",
        "element_keys.js",
        "element_view.js",
        "extract_candidates.js",
        "field_text.js",
        "field_value.js",
        "page_state.js",
        "recorder.js",
        "recorder_control.js",
        "recorder_element.js",
        "scope_facts.js",
    ]

    for path in scripts:
        source = path.read_text(encoding="utf-8")
        allowed = (
            RECORDER_WINDOW_PROPERTIES if path.name == "recorder.js" else ALLOWED_WINDOW_PROPERTIES
        )
        assert source.startswith("// @ts-check\n"), path.name
        assert not re.search(r"^\s*(import|export)\b", source, re.MULTILINE), path.name
        assert "@ts-ignore" not in source, path.name
        assert "@ts-nocheck" not in source, path.name
        assert set(_WINDOW_PROPERTY.findall(source)) <= allowed, path.name


def test_the_recorder_never_reads_field_content() -> None:
    source = _NAMESPACE_BLOCK.sub("", _source("recorder.js"))

    assert _FIELD_CONTENT.findall(source) == []


def test_the_guard_would_catch_a_recorder_reading_a_value() -> None:
    assert _FIELD_CONTENT.findall("const typed = event.target.value;") == [".value"]
    assert _FIELD_CONTENT.findall("node.innerText") == ["innerText"]


def test_the_namespace_bootstrap_is_byte_identical_in_every_script_that_defines_it() -> None:
    blocks = {
        path.name: _NAMESPACE_BLOCK.findall(path.read_text(encoding="utf-8"))
        for path in sorted(PAGE_SCRIPTS.glob("*.js"))
    }
    defining = {name: found for name, found in blocks.items() if found}

    assert set(defining) == {"page_state.js", "recorder.js"}
    assert all(len(found) == 1 for found in defining.values())
    assert len({found[0] for found in defining.values()}) == 1
    for path in PAGE_SCRIPTS.glob("*.js"):
        outside = _NAMESPACE_BLOCK.sub("", path.read_text(encoding="utf-8"))
        assert 'defineProperty(window, "__mendwork"' not in outside, path.name


def test_the_mask_check_is_byte_identical_where_it_is_repeated() -> None:
    facts = _MASK_BLOCK.findall(_source("element_facts.js"))
    field_text = _MASK_BLOCK.findall(_source("field_text.js"))

    assert len(facts) == len(field_text) == 1
    assert facts == field_text


def test_page_scripts_load_from_the_installed_package() -> None:
    scripts = PageScripts.load()

    for source in (
        scripts.page_state,
        scripts.element_keys,
        scripts.element_identity,
        scripts.field_value,
        scripts.recorder,
        scripts.recorder_element,
        scripts.recorder_control,
        scripts.element_facts,
        scripts.element_ancestors,
        scripts.scope_facts,
        scripts.field_text,
        scripts.extract_candidates,
    ):
        assert source.startswith("// @ts-check\n")
