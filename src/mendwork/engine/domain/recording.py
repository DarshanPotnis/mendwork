"""Recordings: what a person did in the browser, as draft steps not yet named or written.

A draft step is a verified step whose values still need decisions: which literal values
become run inputs and what each secret is called. The recorder produces drafts; naming
happens after the person stops; assembly turns drafts plus decisions into version 1 of a
workflow. Draft values never hold a secret: a credential field is recorded as a proposed
secret name, because its value never reached the recorder.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from mendwork.engine.domain.base import DomainModel, LiteralText, Text
from mendwork.engine.domain.checkpoints import Checkpoint
from mendwork.engine.domain.enums import ActionType, CheckpointKind, RiskLevel, SelectorStrategy
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.identifiers import SecretNameField, StepIdField
from mendwork.engine.domain.keys import KeyName


class IgnoredReason(StrEnum):
    """Why an interaction was ignored. Nothing is recorded for it and recording continues."""

    MODIFIED_CLICK = "modified_click"
    """A click with a modifier key or a button other than the primary one."""
    MODIFIED_KEY = "modified_key"
    """Enter, Escape, or Space with a modifier key."""
    DOUBLE_CLICK = "double_click"
    FILE_INPUT = "file_input"
    """File uploads cannot be recorded yet."""
    FRAME = "frame"
    """Interactions inside frames cannot be recorded yet."""
    BUSY = "busy"
    """The interaction started while another step was still being recorded."""
    NOT_ACTIONABLE = "not_actionable"
    """The control was hidden, disabled, or covered, so the click did nothing."""


class InputHint(StrEnum):
    """Why a literal value is proposed as a run input."""

    START_URL = "start_url"
    EMAIL = "email"
    USERNAME = "username"


class LiteralDraft(DomainModel):
    """A value the person typed or chose, or a URL they opened."""

    kind: Literal["literal"] = "literal"
    value: LiteralText
    hint: InputHint | None = None
    """Set when the value is proposed as a run input."""


class SecretDraft(DomainModel):
    """A value typed into a credential field. Only its proposed name is known."""

    kind: Literal["secret"] = "secret"
    proposed_name: SecretNameField
    reason: Text
    """Why the field counts as a credential field, for the person naming the secret."""


DraftValue = Annotated[LiteralDraft | SecretDraft, Field(discriminator="kind")]


class SelectorChoice(DomainModel):
    """Which recorded selector Rung 0 resolved the target with when the step was proven."""

    rank: int = Field(ge=0)
    strategy: SelectorStrategy
    total: int = Field(ge=1)


class DropReason(StrEnum):
    """Why a candidate selector was not kept."""

    NO_MATCH = "no_match"
    DIFFERENT_ELEMENT = "different_element"
    AMBIGUOUS = "ambiguous"
    """It matched several elements, even when scoped."""


class DroppedSelector(DomainModel):
    """A candidate selector that failed verification at record time."""

    strategy: SelectorStrategy
    summary: Text
    """The selector in words, such as ``role_name button 'Save'``."""
    reason: DropReason
    level_counts: tuple[int, ...]


class DroppedCheckpoint(DomainModel):
    """A proposed checkpoint that did not pass at record time, so it was not kept."""

    kind: CheckpointKind
    reason: Text


class DraftStep(DomainModel):
    """One recorded step, verified in the browser, before inputs and secrets are named."""

    index: int = Field(ge=0)
    step_id: StepIdField
    action: ActionType
    description: Text
    """A line a non-developer can check, such as "CLICK the 'Download CSV' button"."""
    intent: Text
    risk: RiskLevel
    risk_reasons: tuple[Text, ...] = ()
    target: Fingerprint | None = None
    value: DraftValue | None = None
    key: KeyName | None = None
    checkpoints: tuple[Checkpoint, ...] = ()
    selector: SelectorChoice | None = None
    dropped_selectors: tuple[DroppedSelector, ...] = ()
    dropped_checkpoints: tuple[DroppedCheckpoint, ...] = ()
    element_key: str | None = None
    """Which element in which document the step acted on, so repeated edits merge."""


class StepRecorded(DomainModel):
    """A step was recorded, or replaced the fill it merged with."""

    kind: Literal["step_recorded"] = "step_recorded"
    step: DraftStep
    replaced: bool = False


class InteractionIgnored(DomainModel):
    """An interaction was ignored; the person should repeat it as a plain action."""

    kind: Literal["interaction_ignored"] = "interaction_ignored"
    reason: IgnoredReason
    during_step: int | None = None
    """For a busy page, the index of the step that was being recorded."""


class NavigationIgnored(DomainModel):
    """The page navigated on its own, outside any step; that is not a step of its own."""

    kind: Literal["navigation_ignored"] = "navigation_ignored"
    url: str


RecordingNotice = Annotated[
    StepRecorded | InteractionIgnored | NavigationIgnored, Field(discriminator="kind")
]


class Recording(DomainModel):
    """Everything captured between opening the start URL and stopping."""

    start_url: str
    steps: tuple[DraftStep, ...]
    notices: tuple[RecordingNotice, ...] = ()

    @property
    def ignored_count(self) -> int:
        """How many interactions were ignored."""
        return sum(1 for notice in self.notices if isinstance(notice, InteractionIgnored))
