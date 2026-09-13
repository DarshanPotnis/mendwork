"""Recording a committed fill or a changed select.

The credential rule decides before any value is read. A field that
``detect_secret_field`` recognises, or that the page reports as masked, becomes a secret
reference and its content is never requested. Only other fields are read, and the read
itself reports a field that became masked instead of returning its content.
"""

from collections.abc import Collection

from pydantic import TypeAdapter, ValidationError

from mendwork.engine.domain.base import LiteralText
from mendwork.engine.domain.checkpoints import FieldHasValue
from mendwork.engine.domain.credentials import detect_secret_field
from mendwork.engine.domain.enums import ActionType, CheckpointKind
from mendwork.engine.domain.recording import DraftStep, DraftValue, LiteralDraft, SecretDraft
from mendwork.engine.ports.browser_types import (
    ElementRef,
    EqualsText,
    FieldExpectation,
    NonEmpty,
)
from mendwork.engine.ports.recording import element_key
from mendwork.engine.ports.recording_types import FillCapture, MaskedField, SelectCapture
from mendwork.engine.recording.checkpoints import Proposal, keep_passing
from mendwork.engine.recording.context import CaptureContext
from mendwork.engine.recording.describe import step_id, target_words
from mendwork.engine.recording.failures import UnusableReason, unusable
from mendwork.engine.recording.naming import default_secret_name, input_hint
from mendwork.engine.recording.targets import RecordedTarget, TargetRecorder
from mendwork.engine.safety.risk import RiskSignals, classify_risk

_LITERAL: TypeAdapter[str] = TypeAdapter(LiteralText)


async def capture_field(
    context: CaptureContext,
    capture: FillCapture | SelectCapture,
    *,
    index: int,
    taken: Collection[str],
) -> DraftStep:
    """Record a field's committed value, or its secret, as a draft step."""
    browser = context.browser
    element = await browser.pin_capture(capture.ref)
    if element is None:
        raise unusable(
            UnusableReason.ELEMENT_GONE,
            "the page navigated before a field's change could be recorded",
        )
    try:
        target = await TargetRecorder(context).record(element)
        if isinstance(capture, FillCapture):
            return await _fill(context, capture, element, target, index=index, taken=taken)
        return await _select(context, capture, element, target, index=index, taken=taken)
    finally:
        await browser.release([element])


async def _fill(
    context: CaptureContext,
    capture: FillCapture,
    element: ElementRef,
    target: RecordedTarget,
    *,
    index: int,
    taken: Collection[str],
) -> DraftStep:
    fingerprint = target.fingerprint
    reason = detect_secret_field(fingerprint)
    if reason is None and target.facts.masked:
        reason = "it is masked on the page"
    plain: str | None = None
    if reason is None:
        text = await context.browser.read_field_text(element)
        if isinstance(text, MaskedField):
            reason = "it was masked when its value was read"
        else:
            plain = _literal(text.value)
    value: DraftValue
    expectation: FieldExpectation
    if plain is None:
        value = SecretDraft(
            proposed_name=default_secret_name(fingerprint), reason=reason or "it is masked"
        )
        expectation = NonEmpty()
    else:
        value = LiteralDraft(value=plain, hint=input_hint(fingerprint, plain))
        expectation = EqualsText(value=plain)
    proposal = Proposal(
        kind=CheckpointKind.FIELD_HAS_VALUE,
        alternatives=(FieldHasValue(kind=CheckpointKind.FIELD_HAS_VALUE),),
    )
    checkpoints, dropped = await keep_passing(
        (proposal,), context.checkpoints(target=element, expectation=expectation)
    )
    return _draft(
        context, capture, target, ActionType.FILL, value, index=index, taken=taken
    ).model_copy(update={"checkpoints": checkpoints, "dropped_checkpoints": dropped})


async def _select(
    context: CaptureContext,
    capture: SelectCapture,
    element: ElementRef,
    target: RecordedTarget,
    *,
    index: int,
    taken: Collection[str],
) -> DraftStep:
    unreadable = detect_secret_field(target.fingerprint) is not None or target.facts.masked
    text = None if unreadable else await context.browser.read_field_text(element)
    if text is None or isinstance(text, MaskedField):
        raise unusable(
            UnusableReason.UNRECORDABLE_TARGET,
            "a list that looks like a credential field cannot be recorded, because a select "
            "step cannot use a secret",
        )
    value = LiteralDraft(value=_literal(text.value))
    return _draft(context, capture, target, ActionType.SELECT, value, index=index, taken=taken)


def _draft(
    context: CaptureContext,
    capture: FillCapture | SelectCapture,
    target: RecordedTarget,
    action: ActionType,
    value: DraftValue,
    *,
    index: int,
    taken: Collection[str],
) -> DraftStep:
    identity = target.identity
    label = target.fingerprint.label_text or identity.name
    description, intent = target_words(
        action, role=identity.role, name=label, tag=target.facts.tag, key=None
    )
    assessment = classify_risk(
        RiskSignals(action=action, role=identity.role, name=identity.name), context.config.risk
    )
    return DraftStep(
        index=index,
        step_id=step_id(action.value, label or target.facts.tag, taken),
        action=action,
        description=description,
        intent=intent,
        risk=assessment.level,
        risk_reasons=assessment.reasons,
        target=target.fingerprint,
        value=value,
        selector=target.choice,
        dropped_selectors=target.dropped,
        element_key=element_key(capture.ref),
    )


def _literal(value: str) -> str:
    try:
        return _LITERAL.validate_python(value)
    except ValidationError as error:
        raise unusable(
            UnusableReason.UNRECORDABLE_TARGET,
            "the value typed into the field is too long or contains characters a workflow "
            "cannot store",
        ) from error
