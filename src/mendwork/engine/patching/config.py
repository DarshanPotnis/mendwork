"""Patching tuning, handed to the engine by the app that builds it from Settings.

There are no defaults here: Settings is the only place a default may live.
"""

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import PromotionPolicy


class PatchingConfig(DomainModel):
    """When verified heals become versions, and how hard publishing one tries."""

    promotion: PromotionPolicy
    successes_required: int = Field(ge=2, le=100)
    """Under ``after_n_successes``, how many succeeded runs must verify a heal; 1 would be
    ``immediate``."""
    publish_attempts: int = Field(ge=1, le=20)
    """How many times a heal is placed on the latest version again when another version was
    published first."""
