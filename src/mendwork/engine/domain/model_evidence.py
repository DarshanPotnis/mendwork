"""Model evidence: what Rung 3 showed a model, what the model answered, and what that cost.

A model is only ever shown descriptions of candidates the ladder already pinned and scored, so
the evidence records exactly those descriptions, numbered as the model saw them. Costs are
estimates from a configured price table: a call whose price is unknown counts as unpriced,
never as free.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.scores import CandidateId, Score

TokenCount = Annotated[int, Field(ge=0)]
Usd = Annotated[Decimal, Field(ge=0)]


class ModelUsage(DomainModel):
    """One model call: tokens, time, HTTP requests, and estimated price."""

    provider: str
    model: str
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    latency_ms: int = Field(ge=0)
    http_attempts: int = Field(ge=0)
    """HTTP requests made for this one call, retries included; 0 when none was sent."""
    estimated_cost_usd: Usd | None = None
    """None when the model has no configured price; 0 for a local model."""


class ModelUsageTotals(DomainModel):
    """Every model call a run made, added up."""

    calls: int = Field(default=0, ge=0)
    input_tokens: TokenCount = 0
    output_tokens: TokenCount = 0
    latency_ms: int = Field(default=0, ge=0)
    estimated_cost_usd: Usd = Decimal(0)
    """The priced calls' estimated cost; unpriced calls are counted, not guessed."""
    unpriced_calls: int = Field(default=0, ge=0)

    def combined(self, other: "ModelUsageTotals") -> "ModelUsageTotals":
        """These totals and another execution's, added up."""
        return ModelUsageTotals(
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            latency_ms=self.latency_ms + other.latency_ms,
            estimated_cost_usd=self.estimated_cost_usd + other.estimated_cost_usd,
            unpriced_calls=self.unpriced_calls + other.unpriced_calls,
        )

    def plus(self, usage: ModelUsage) -> "ModelUsageTotals":
        """These totals with one more call."""
        return ModelUsageTotals(
            calls=self.calls + 1,
            input_tokens=self.input_tokens + (usage.input_tokens or 0),
            output_tokens=self.output_tokens + (usage.output_tokens or 0),
            latency_ms=self.latency_ms + usage.latency_ms,
            estimated_cost_usd=self.estimated_cost_usd + (usage.estimated_cost_usd or Decimal(0)),
            unpriced_calls=self.unpriced_calls + (1 if usage.estimated_cost_usd is None else 0),
        )


class CandidateDescription(DomainModel):
    """A candidate as a model is shown it: its kind and its texts, never markup or position."""

    kind: str
    name: str | None = None
    label: str | None = None
    text: str | None = None
    """Visible text, only when it differs from the name."""
    nearby_text: tuple[str, ...] = ()


class ShownCandidate(DomainModel):
    """One numbered line of the list a model chose from."""

    number: int = Field(ge=1)
    candidate: CandidateId
    """The candidate's id in Rung 2's ranking for the same attempt."""
    description: CandidateDescription
    similarity: Score
    """Rung 2's score, shown to the model as a similarity."""


class ModelCallPurpose(StrEnum):
    """Why a call was made."""

    CHOOSE = "choose"
    REPAIR = "repair"
    """A second call after a reply in the wrong shape."""


class ModelCallOutcome(StrEnum):
    """What a call came back with."""

    ANSWERED = "answered"
    INVALID_OUTPUT = "invalid_output"
    UNAVAILABLE = "unavailable"


class ModelCall(DomainModel):
    """One call to the model."""

    purpose: ModelCallPurpose
    outcome: ModelCallOutcome
    problem: str | None = None
    """For a reply in the wrong shape, what was wrong, in fixed words."""
    usage: ModelUsage


class BudgetScope(StrEnum):
    """Which model-call budget ran out."""

    RUN = "run"
    DAY = "day"


class BudgetStop(DomainModel):
    """A model call that was not made because a budget was used up."""

    scope: BudgetScope
    limit: int = Field(ge=0)
    resets_at: datetime | None = None
    """For the daily budget, when the count starts again."""
    detail: str


class ModelChoiceEvidence(DomainModel):
    """Everything Rung 3 sent a model and got back, for one heal attempt."""

    prompt_version: str
    shown: tuple[ShownCandidate, ...] = ()
    """The numbered list the model chose from, empty when it was not asked."""
    not_shown: int = Field(default=0, ge=0)
    """Eligible candidates ranked below the ones shown."""
    ineligible: int = Field(default=0, ge=0)
    """Candidates never shown: refused by a safety rule, or sharing no wording or identity
    attributes with the recording."""
    calls: tuple[ModelCall, ...] = ()
    choice: int | None = None
    """The number the model answered, when it answered one."""
    confidence: Score | None = None
    """The model's own confidence. Evidence only: no decision reads it."""
    reason: str | None = None
    unavailable: str | None = None
    """Why the provider could not answer, when it could not."""
    budget: BudgetStop | None = None
