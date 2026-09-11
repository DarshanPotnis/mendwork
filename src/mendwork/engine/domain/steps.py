"""Steps, one model per action, so each action's shape is enforced by its type.

A click cannot carry a value and a navigation cannot carry a target, because those fields
do not exist on those models. Rules that span fields (credential fields need secrets)
are checked where the offending field is, so errors point at the exact line.
"""

from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from mendwork.engine.domain.base import DomainModel, Text
from mendwork.engine.domain.checkpoints import Checkpoint
from mendwork.engine.domain.credentials import detect_secret_field
from mendwork.engine.domain.enums import ActionType, RiskLevel
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.identifiers import StepIdField
from mendwork.engine.domain.keys import KeyName
from mendwork.engine.domain.limits import CHECKPOINTS_MAX_ITEMS
from mendwork.engine.domain.values import (
    LiteralValue,
    NonSecretValueRef,
    SecretValue,
    ValueRef,
    check_http_url,
)

Checkpoints = Annotated[tuple[Checkpoint, ...], Field(max_length=CHECKPOINTS_MAX_ITEMS)]


class _Step(DomainModel):
    id: StepIdField
    """Unique within the workflow and stable across versions: a heal never changes it."""
    intent: Text
    """What the step is for, in words a reviewer understands."""
    action: ActionType
    risk: RiskLevel
    """What the action changes: safe reads, caution changes session or form state,
    irreversible changes stored data or affects others."""

    if TYPE_CHECKING:
        # Declared by each concrete step, last, so the checkpoints close the step in files.
        checkpoints: Checkpoints

    @model_validator(mode="after")
    def _reject_duplicate_checkpoints(self) -> Self:
        for index, checkpoint in enumerate(self.checkpoints):
            first = self.checkpoints.index(checkpoint)
            if first != index:
                raise ValueError(f"checkpoints[{index}] duplicates checkpoints[{first}]")
        return self


class NavigateStep(_Step):
    """Load a URL in the current tab."""

    action: Literal[ActionType.NAVIGATE]
    value: NonSecretValueRef
    """The URL: a literal absolute http(s) URL or a url input."""
    checkpoints: Checkpoints = ()

    @field_validator("value")
    @classmethod
    def _literal_must_be_a_url(cls, value: NonSecretValueRef) -> NonSecretValueRef:
        if isinstance(value, LiteralValue):
            check_http_url(value.value)
        return value


class ClickStep(_Step):
    """Click the target."""

    action: Literal[ActionType.CLICK]
    target: Fingerprint
    checkpoints: Checkpoints = ()


class FillStep(_Step):
    """Replace the target field's content with a value."""

    action: Literal[ActionType.FILL]
    target: Fingerprint
    value: ValueRef
    checkpoints: Checkpoints = ()

    @field_validator("value")
    @classmethod
    def _credentials_need_secrets(cls, value: ValueRef, info: ValidationInfo) -> ValueRef:
        target = info.data.get("target")
        if isinstance(target, Fingerprint) and not isinstance(value, SecretValue):
            reason = detect_secret_field(target)
            if reason is not None:
                raise ValueError(
                    f"the target looks like a password or secret field ({reason}), so its "
                    f"value must be a secret reference such as {{kind: secret, name: ...}}, "
                    f"not a {value.kind} value"
                )
        return value


class SelectStep(_Step):
    """Choose the option whose visible label is the value."""

    action: Literal[ActionType.SELECT]
    target: Fingerprint
    value: NonSecretValueRef
    """The option's visible label."""
    checkpoints: Checkpoints = ()


class PressStep(_Step):
    """Press a key, on the target if there is one, otherwise on the focused page."""

    action: Literal[ActionType.PRESS]
    target: Fingerprint | None = None
    key: KeyName
    """A named key or character, optionally with modifiers, such as 'Enter' or 'Control+a'."""
    checkpoints: Checkpoints = ()


Step = Annotated[
    NavigateStep | ClickStep | FillStep | SelectStep | PressStep,
    Field(discriminator="action"),
]


def step_target(step: Step) -> Fingerprint | None:
    """The step's target, if its action has one."""
    match step:
        case NavigateStep():
            return None
        case ClickStep() | FillStep() | SelectStep() | PressStep():
            return step.target


def step_value(step: Step) -> ValueRef | None:
    """The step's value reference, if its action takes one."""
    match step:
        case NavigateStep() | FillStep() | SelectStep():
            return step.value
        case ClickStep() | PressStep():
            return None
