"""Static guards on product code: no chaos ground truth, and page scripts that follow the rules."""

import re
from pathlib import Path
from typing import Final

from mendwork.adapters.browser_playwright.scripts import PageScripts

REPO: Final = Path(__file__).resolve().parents[2]
PRODUCT: Final = REPO / "src" / "mendwork"
PAGE_SCRIPTS: Final = PRODUCT / "adapters" / "browser_playwright" / "js"
# A page script may touch exactly one global of its own, and read the window's computed style.
_OTHER_WINDOW_PROPERTY: Final = re.compile(r"\bwindow\.(?!__mendwork\b|getComputedStyle\b)")


def _product_files() -> list[Path]:
    return sorted(
        path
        for path in PRODUCT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )


def test_no_product_file_refers_to_the_chaos_ground_truth() -> None:
    files = _product_files()
    assert len(files) > 80, "the guard found too few files to mean anything"

    offenders = [
        str(path.relative_to(REPO))
        for path in files
        if "__chaos" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_every_page_script_is_type_checked_self_contained_and_namespaced() -> None:
    scripts = sorted(PAGE_SCRIPTS.glob("*.js"))
    assert [path.name for path in scripts] == [
        "element_identity.js",
        "element_keys.js",
        "field_value.js",
        "page_state.js",
    ]

    for path in scripts:
        source = path.read_text(encoding="utf-8")
        assert source.startswith("// @ts-check\n"), path.name
        assert not re.search(r"^\s*(import|export)\b", source, re.MULTILINE), path.name
        assert "@ts-ignore" not in source, path.name
        assert "@ts-nocheck" not in source, path.name
        assert not _OTHER_WINDOW_PROPERTY.search(source), path.name


def test_page_scripts_load_from_the_installed_package() -> None:
    scripts = PageScripts.load()

    for source in (
        scripts.page_state,
        scripts.element_keys,
        scripts.element_identity,
        scripts.field_value,
    ):
        assert source.startswith("// @ts-check\n")
