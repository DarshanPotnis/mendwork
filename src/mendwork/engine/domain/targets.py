"""Target evidence: what the selectors found and what identity the element had.

Shared by run records and heal reports, which both explain how a step's target was found or
why it was not.
"""

from enum import StrEnum

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import SelectorStrategy


class SelectorOutcome(StrEnum):
    """What one ranked selector found at Rung 0."""

    HIT = "hit"
    """Exactly one visible element at every scope level."""
    NONE = "none"
    """Some level matched no visible element."""
    MANY = "many"
    """Some level matched several visible elements."""


class SelectorReport(DomainModel):
    """One selector's result at Rung 0 or Rung 1."""

    rank: int = Field(ge=0)
    strategy: SelectorStrategy
    level_counts: tuple[int, ...]
    """Visible matches per scope level, outermost first, stopping at the first level that
    did not match exactly one element."""
    outcome: SelectorOutcome
    element: int | None = None
    """For a hit, which distinct element it found, numbered in order of first appearance."""


class IdentityReport(DomainModel):
    """An element's identity as the page reported it."""

    tag: str
    input_type: str | None = None
    role: str | None = None
    name: str
    confirmed: bool | None = None
    """Whether Playwright's own role locator agrees; None when there is no role to confirm."""


class TargetEvidence(DomainModel):
    """Everything observed while resolving a step's target."""

    selectors: tuple[SelectorReport, ...]
    resolved_rank: int | None = None
    """The best-ranked selector that hit, when the hits agreed."""
    identity: IdentityReport | None = None
    """The identity of the element the hits agreed on, or of the healed element."""
    elements: tuple[IdentityReport, ...] = ()
    """One identity per distinct element, when the hits disagreed."""
    differences: tuple[str, ...] = ()
    """How the found identity differs from the recorded one, for a drifted match."""
    healed_rung: int | None = Field(default=None, ge=1, le=3)
    """The rung that found the target, when the recorded selectors did not."""
