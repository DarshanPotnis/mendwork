"""Words every heal output module shares: the indent and how an element is named."""

from typing import Final

from mendwork.engine.domain.targets import IdentityReport

INDENT: Final = "      "


def describe(identity: IdentityReport | None) -> str:
    """An element in words, such as ``button "Download CSV"``."""
    if identity is None:
        return "an element"
    return f'{identity.role or identity.tag} "{identity.name}"'
