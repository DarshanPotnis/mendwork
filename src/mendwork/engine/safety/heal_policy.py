"""Safety rules for heals: what refuses a candidate whatever it scores, and what a heal may do.

Every rule is a pure function, so each is a table a test can pin; the ladder composes them.

- **Danger words** are read through the risk classifier's own function and vocabulary, so the
  healer and the classifier can never disagree about what is destructive.
- **Identifiers**: a candidate whose name carries a different identifier than the recording
  ("INV-7780" where "INV-2231" was recorded) is another row's control, however similar.
- **Kinds** (``heal_kinds``): a control may change kind only to one activated the same way (a
  button and a link), and only when the step checks the effect that activation must have.
- **Credentials** go only into masked fields, and plain values never do.
- **Irreversible steps** never act on a heal; a failed irreversible action is never retried.
- **Verification**: a heal is allowed only when the step has a checkpoint that can prove it.
"""

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Final

from mendwork.engine.domain.checkpoints import (
    Checkpoint,
    DownloadCompleted,
    ElementVisible,
    FieldHasValue,
    ResponseReceived,
    TextPresent,
    UrlMatches,
)
from mendwork.engine.domain.credentials import detect_secret_field, tokenize
from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.steps import (
    FillStep,
    NavigateStep,
    SelectStep,
    Step,
)
from mendwork.engine.domain.values import SecretValue
from mendwork.engine.safety.risk import (
    RiskSignals,
    RiskVocabulary,
    authentication_reason,
    danger_words_in,
)

EFFECT_CHECKPOINTS: Final = (
    UrlMatches,
    ElementVisible,
    TextPresent,
    DownloadCompleted,
    ResponseReceived,
)
"""Checkpoints that observe what an action did. ``no_error_banner`` proves only that nothing
visibly went wrong, and ``field_has_value`` only that a value landed in a field."""


class FailedHealRecovery(StrEnum):
    """What happens after acting on a healed target fails the step's checkpoints."""

    RESTORE = "restore"
    """Put the page back to its last known-good state, then try another candidate."""
    RESET_THEN_RESTORE = "reset_then_restore"
    """Clear what the failed action typed first, then restore."""
    NEEDS_REVIEW = "needs_review"
    """Stop: the action cannot be undone or repeated, so a person must check it."""


def introduced_danger(
    recorded: Iterable[str | None], found: Iterable[str | None], vocabulary: RiskVocabulary
) -> tuple[str, ...]:
    """Danger words in the found control's texts that none of the recorded texts had."""
    before = {word for text in recorded for word in danger_words_in(text, vocabulary)}
    after = {word for text in found for word in danger_words_in(text, vocabulary)}
    return tuple(sorted(after - before))


def identifier_tokens(texts: Iterable[str | None]) -> frozenset[str]:
    """The numbers in a name: order numbers, invoice ids, dates, amounts."""
    return frozenset(
        token for text in texts for token in tokenize(text or "") if any(c.isdigit() for c in token)
    )


def identifier_mismatch(
    recorded: Iterable[str | None], found: Iterable[str | None]
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """The recorded and found identifiers when the found control names a different one.

    Mismatched only when each side has an identifier the other lacks: "Show invoice INV-2231"
    still matches "Open invoice INV-2231", and "Open invoice INV-7780" does not.
    """
    before = identifier_tokens(recorded)
    after = identifier_tokens(found)
    if before - after and after - before:
        return tuple(sorted(before)), tuple(sorted(after))
    return None


def is_credential_step(step: FillStep) -> bool:
    """Whether a fill types a credential: a secret value, or a field that looks like one."""
    return isinstance(step.value, SecretValue) or detect_secret_field(step.target) is not None


def credential_mismatch(credential_step: bool, found_masked: bool) -> bool:
    """Whether a heal would type a credential into a visible field, or plain text into a
    masked one. The first would show the credential in the page and in screenshots."""
    return credential_step != found_masked


def authentication_step(
    step: Step, vocabulary: RiskVocabulary, *, submits_password_form: bool
) -> str | None:
    """Why a step authenticates, or None. Such steps get one heal attempt per run, because
    repeated attempts can lock the account.

    A fill of a credential, or a click or key the risk classifier reads as changing the
    session. ``submits_password_form`` is what the found element does in the live page.
    """
    if isinstance(step, FillStep):
        return "fills a credential" if is_credential_step(step) else None
    if isinstance(step, NavigateStep | SelectStep) or step.target is None:
        return None
    target = step.target
    return authentication_reason(
        RiskSignals(
            action=step.action,
            role=target.role.value if target.role is not None else None,
            name=target.accessible_name or target.text,
            form_submit=submits_password_form,
            form_has_password=submits_password_form,
        ),
        vocabulary,
    )


def has_effect_checkpoint(checkpoints: Sequence[Checkpoint]) -> bool:
    """Whether any checkpoint observes what the action did."""
    return any(isinstance(checkpoint, EFFECT_CHECKPOINTS) for checkpoint in checkpoints)


def is_verifiable(step: Step) -> bool:
    """Whether the step has a checkpoint that could prove a heal.

    A fill is proven by ``field_has_value`` or an effect checkpoint; any other action only by
    an effect checkpoint. A step without one abstains rather than healing silently.
    """
    if isinstance(step, FillStep) and any(
        isinstance(checkpoint, FieldHasValue) for checkpoint in step.checkpoints
    ):
        return True
    return has_effect_checkpoint(step.checkpoints)


def heal_may_act(risk: RiskLevel) -> bool:
    """Whether a step of this risk may act on a healed target without a person's approval."""
    return risk is not RiskLevel.IRREVERSIBLE


def recovery_after_failed_heal(risk: RiskLevel) -> FailedHealRecovery:
    """What to do after acting on a healed target failed the step's checkpoints."""
    match risk:
        case RiskLevel.SAFE:
            return FailedHealRecovery.RESTORE
        case RiskLevel.CAUTION:
            return FailedHealRecovery.RESET_THEN_RESTORE
        case RiskLevel.IRREVERSIBLE:
            return FailedHealRecovery.NEEDS_REVIEW
