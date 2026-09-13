"""Live candidates: the elements a heal compares with a step's recorded fingerprint.

The browser adapter scans the page for visible elements the step's action could receive,
pins each one, and reports its identity and facts, read without any field's content. The
engine decides everything else: which candidates are compatible, how similar each is, and
whether one may be acted on.
"""

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.ports.browser_types import ElementIdentity, ElementRef
from mendwork.engine.ports.element_types import ElementFacts


class CandidateQuery(DomainModel):
    """Which elements to scan for, and how many to pin at most."""

    action: ActionType
    limit: int = Field(ge=1)


class LiveCandidate(DomainModel):
    """One pinned element, its identity (unconfirmed), and its facts."""

    element: ElementRef
    identity: ElementIdentity
    facts: ElementFacts


class CandidateScan(DomainModel):
    """What a scan found, in document order."""

    candidates: tuple[LiveCandidate, ...]
    total: int = Field(ge=0)
    """How many visible, action-compatible elements the page has, including any not pinned."""

    @property
    def capped(self) -> bool:
        """Whether the page had more candidates than the scan was allowed to pin."""
        return self.total > len(self.candidates)
