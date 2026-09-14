"""Estimated cost of a model call, from a price table in Settings.

A local model costs nothing. A hosted model without a configured price, or a call whose
provider did not report token counts, has an unknown cost: it is recorded as unpriced, never
guessed and never counted as free.
"""

from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

_MILLION: Final = Decimal(1_000_000)


class ModelPrice(BaseModel):
    """A hosted model's list price per million tokens, in US dollars."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_usd_per_million_tokens: Decimal = Field(ge=0)
    output_usd_per_million_tokens: Decimal = Field(ge=0)


def estimated_cost(
    *,
    local: bool,
    price: ModelPrice | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> Decimal | None:
    """The call's estimated cost in US dollars, or None when it cannot be known."""
    if local:
        return Decimal(0)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return (
        price.input_usd_per_million_tokens * input_tokens
        + price.output_usd_per_million_tokens * output_tokens
    ) / _MILLION
