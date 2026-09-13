"""Page scripts: shipped as package data beside this module and loaded at runtime.

Each ``js/*.js`` file is type-checked by ``make jscheck``. All but one are a single function
expression evaluated with its arguments by Playwright; ``recorder.js`` is an init script
that runs itself in every new document. JavaScript never lives in Python strings.
"""

from dataclasses import dataclass
from importlib.resources import files
from typing import Final

SCRIPT_PACKAGE: Final = "mendwork.adapters.browser_playwright"


@dataclass(frozen=True, slots=True)
class PageScripts:
    """The source of every page script the adapter evaluates or installs."""

    page_state: str
    element_keys: str
    element_identity: str
    field_value: str
    recorder: str
    recorder_element: str
    recorder_control: str
    element_facts: str
    element_ancestors: str
    scope_facts: str
    field_text: str
    extract_candidates: str

    @classmethod
    def load(cls) -> "PageScripts":
        """Read the scripts from the installed package. Blocking; call it off the event loop."""
        directory = files(SCRIPT_PACKAGE) / "js"

        def read(name: str) -> str:
            return (directory / f"{name}.js").read_text(encoding="utf-8")

        return cls(
            page_state=read("page_state"),
            element_keys=read("element_keys"),
            element_identity=read("element_identity"),
            field_value=read("field_value"),
            recorder=read("recorder"),
            recorder_element=read("recorder_element"),
            recorder_control=read("recorder_control"),
            element_facts=read("element_facts"),
            element_ancestors=read("element_ancestors"),
            scope_facts=read("scope_facts"),
            field_text=read("field_text"),
            extract_candidates=read("extract_candidates"),
        )
