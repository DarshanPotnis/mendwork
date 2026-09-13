"""Recording tuning, handed to the engine by the app that builds it from Settings."""

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.replay.config import RetryPolicy
from mendwork.engine.safety.risk import RiskVocabulary


class RecordingConfig(DomainModel):
    """Every timeout, bound, and vocabulary the recorder uses. There are no defaults here."""

    step_timeout_ms: int = Field(ge=1)
    """Verifying a step's target and performing its action."""
    settle_timeout_ms: int = Field(ge=1)
    settle_quiet_frames: int = Field(ge=1, le=60)
    navigation_timeout_ms: int = Field(ge=1)
    checkpoint_timeout_ms: int = Field(ge=1)
    """How long a proposed checkpoint may wait while it is verified at record time."""
    scope_ancestors_max: int = Field(ge=1, le=20)
    landmarks_max: int = Field(ge=1, le=500)
    retry: RetryPolicy
    risk: RiskVocabulary
