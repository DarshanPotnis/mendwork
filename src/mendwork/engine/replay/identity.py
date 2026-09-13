"""The Rung 0 identity check: is the element the selectors found the one that was recorded?

A selector surviving a release is not proof the control did: ids and test ids routinely
outlive a relabel, which is how "Export ledger" becomes "Delete ledger" under the same
``data-testid``. So the found element's identity is compared with the fingerprint:

- with a recorded role: role and accessible name;
- without one (password and date inputs have no ARIA role): tag, type, and name.

Names are compared after Unicode NFKC normalization, case folding, and whitespace
collapsing, so "Export  ledger" and "export ledger" are the same name. An identity the page
computed but Playwright could not confirm also counts as different. Any difference is a
drifted match, which Phase 3 never acts on.
"""

import unicodedata
from enum import StrEnum
from typing import Final

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.targets import IdentityReport
from mendwork.engine.ports.browser_types import ElementIdentity

# HTML's defaults when the type attribute is absent.
_DEFAULT_TYPES: Final = {"input": "text", "button": "submit"}


class Difference(StrEnum):
    """One way a found element's identity differs from the recorded one."""

    ROLE = "role"
    TAG = "tag"
    INPUT_TYPE = "input_type"
    NAME = "accessible_name"
    UNCONFIRMED = "unconfirmed"


def normalize_name(value: str | None) -> str:
    """Compare-ready form of an accessible name: NFKC, case-folded, whitespace collapsed."""
    folded = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", value or "").casefold())
    return " ".join(folded.split())


def effective_type(tag: str, type_attribute: str | None) -> str | None:
    """The type an element behaves as, applying HTML's default for inputs and buttons."""
    tag = tag.lower()
    if type_attribute:
        return type_attribute.lower()
    return _DEFAULT_TYPES.get(tag)


def identity_differences(
    fingerprint: Fingerprint, found: ElementIdentity
) -> tuple[Difference, ...]:
    """Every way the found element differs from the recorded fingerprint, in a fixed order."""
    differences: list[Difference] = []
    if fingerprint.role is not None:
        if found.role != fingerprint.role.value:
            differences.append(Difference.ROLE)
    else:
        if found.tag.lower() != fingerprint.tag:
            differences.append(Difference.TAG)
        recorded_type = effective_type(fingerprint.tag, fingerprint.attributes.type)
        if effective_type(found.tag, found.input_type) != recorded_type:
            differences.append(Difference.INPUT_TYPE)
    if normalize_name(found.name) != normalize_name(fingerprint.accessible_name):
        differences.append(Difference.NAME)
    if found.confirmed is False:
        differences.append(Difference.UNCONFIRMED)
    return tuple(differences)


def recorded_identity(fingerprint: Fingerprint) -> IdentityReport:
    """The fingerprint's identity, shaped like a found one, for side-by-side evidence."""
    return IdentityReport(
        tag=fingerprint.tag,
        input_type=fingerprint.attributes.type,
        role=fingerprint.role.value if fingerprint.role is not None else None,
        name=fingerprint.accessible_name or "",
    )


def same_identity(first: ElementIdentity, second: ElementIdentity) -> bool:
    """Whether two page readings describe the same identity, ignoring confirmation."""
    return (
        first.tag == second.tag
        and first.input_type == second.input_type
        and first.role == second.role
        and normalize_name(first.name) == normalize_name(second.name)
    )
