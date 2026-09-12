"""Page scripts: shipped as package data beside this module and loaded at runtime.

Each ``js/*.js`` file is a single function expression, type-checked by ``make jscheck``
and evaluated with its arguments by Playwright. JavaScript never lives in Python strings.
"""

from dataclasses import dataclass
from importlib.resources import files
from typing import Final

SCRIPT_PACKAGE: Final = "mendwork.adapters.browser_playwright"


@dataclass(frozen=True, slots=True)
class PageScripts:
    """The source of every page script the adapter evaluates."""

    page_state: str
    element_keys: str
    element_identity: str
    field_value: str

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
        )
