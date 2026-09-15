"""Escape-by-default HTML for the run report.

Every piece of text is escaped when it becomes markup; the only way to write a tag is ``element``,
whose tag and attribute names must be plain lowercase words. Page text, names, error messages, and
reasons can therefore never become markup, whatever a site or a model put in them.
"""

import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

_NAME: Final = re.compile(r"[a-z][a-z0-9-]*")
VOID_TAGS: Final = frozenset({"img", "meta", "br"})


@dataclass(frozen=True, slots=True)
class Markup:
    """HTML that is safe to place in the document as it is."""

    html: str


def text(value: str) -> Markup:
    """Text, escaped."""
    return Markup(html.escape(value, quote=True))


def join(parts: Iterable[Markup]) -> Markup:
    """Markup one after another."""
    return Markup("".join(part.html for part in parts))


def element(
    tag: str,
    attributes: Mapping[str, str | None] | None = None,
    *children: Markup | str | None,
) -> Markup:
    """A tag with escaped attribute values and children; plain strings are escaped, None skipped.

    Raises ValueError for a tag or attribute name that is not a plain lowercase word.
    """
    for name in (tag, *(attributes or {})):
        if _NAME.fullmatch(name) is None:
            raise ValueError(f"not a plain tag or attribute name: {name!r}")
    rendered = "".join(
        f' {name}="{html.escape(value, quote=True)}"'
        for name, value in (attributes or {}).items()
        if value is not None
    )
    if tag in VOID_TAGS:
        return Markup(f"<{tag}{rendered}>")
    inner = "".join(
        child.html if isinstance(child, Markup) else html.escape(child, quote=True)
        for child in children
        if child is not None
    )
    return Markup(f"<{tag}{rendered}>{inner}</{tag}>")
