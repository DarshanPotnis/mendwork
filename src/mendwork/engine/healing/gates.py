"""The gates an accepted heal passes before anything acts on it.

In order: the step must have a checkpoint that can prove the heal; an irreversible step never
acts on a heal and stops with a proposal instead; and the step must be within its heal attempt
limit, which is one for an authentication step. Each gate returns why the heal stops, or None.

The gates whose answer does not depend on which element is chosen are also checked before a
model is asked (``before_asking``), so no call is spent on a step that could not act anyway.
"""

from collections.abc import Callable
from dataclasses import dataclass

from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.heals import AbstentionReason, HealProposal
from mendwork.engine.domain.steps import Step
from mendwork.engine.errors import ApprovalRequired, HealAbstained, MendworkError
from mendwork.engine.healing.config import HealingConfig
from mendwork.engine.healing.context import AcceptedHeal
from mendwork.engine.safety.heal_policy import authentication_step, is_verifiable
from mendwork.engine.safety.secret_scrub import SecretScrubber


@dataclass(frozen=True, slots=True)
class HealStop:
    """Why an accepted heal will not be acted on."""

    error: MendworkError
    abstention: AbstentionReason | None = None
    proposal: HealProposal | None = None


def before_asking(step: Step, *, used: int, config: HealingConfig) -> AbstentionReason | None:
    """A gate that stops the step whatever a model would choose, or None.

    The step needs a checkpoint that can prove a heal, and a heal attempt left. The limit is an
    authentication step's when the recording itself reads as one, and the ordinary limit
    otherwise, which is never lower; whether the chosen element submits a password form is only
    known once it is chosen, and is checked then like any other heal.
    """
    if not is_verifiable(step):
        return AbstentionReason.UNVERIFIABLE
    authentication = authentication_step(step, config.vocabulary, submits_password_form=False)
    limit = config.authentication_max_attempts if authentication else config.max_attempts
    if used < limit:
        return None
    if authentication is not None:
        return AbstentionReason.AUTHENTICATION_LIMIT
    return AbstentionReason.ATTEMPTS_EXHAUSTED


def before_acting(
    step: Step,
    accepted: AcceptedHeal,
    *,
    used: int,
    config: HealingConfig,
    may_act: Callable[[RiskLevel], bool],
    scrubber: SecretScrubber,
) -> HealStop | None:
    """The first gate that stops an accepted heal, or None when it may be acted on."""
    if not is_verifiable(step):
        return HealStop(
            HealAbstained(
                "a heal was found, but this step has no checkpoint that could prove it, so "
                "nothing was acted on",
                reason=AbstentionReason.UNVERIFIABLE.value,
                rung=accepted.rung,
                score=accepted.scored.score,
            ),
            abstention=AbstentionReason.UNVERIFIABLE,
        )
    if not may_act(step.risk):
        proposal = HealProposal(
            rung=accepted.rung,
            candidate=accepted.proposal_candidate(scrubber),
            margin=accepted.report.margin,
            reason="the step is irreversible, so a healed target needs a person's approval "
            "before anything acts on it",
            model=accepted.report.model,
        )
        error = ApprovalRequired(
            "a heal was found for this irreversible step; nothing acts on it without a "
            "person's approval",
            reason="irreversible_step",
            rung=accepted.rung,
            score=accepted.scored.score,
            margin=accepted.report.margin,
        )
        return HealStop(error, proposal=proposal)
    return over_limit(step, accepted, used=used, config=config)


def over_limit(
    step: Step, accepted: AcceptedHeal, *, used: int, config: HealingConfig
) -> HealStop | None:
    """Whether the step has used every heal attempt it is allowed."""
    facts = accepted.scored.candidate.facts
    authentication = authentication_step(
        step,
        config.vocabulary,
        submits_password_form=facts.form_submit and facts.form_has_password,
    )
    limit = config.authentication_max_attempts if authentication else config.max_attempts
    if used < limit:
        return None
    if authentication is not None:
        return HealStop(
            HealAbstained(
                f"this step {authentication}, and authentication steps get {limit} heal "
                f"attempt{'' if limit == 1 else 's'} per run so the account is not locked; "
                f"{used} {'was' if used == 1 else 'were'} used",
                reason=AbstentionReason.AUTHENTICATION_LIMIT.value,
                limit=limit,
                used=used,
            ),
            abstention=AbstentionReason.AUTHENTICATION_LIMIT,
        )
    return HealStop(
        HealAbstained(
            f"{used} healed target{'' if used == 1 else 's'} failed this step's checkpoints, the "
            "most one step may try",
            reason=AbstentionReason.ATTEMPTS_EXHAUSTED.value,
            limit=limit,
            used=used,
        ),
        abstention=AbstentionReason.ATTEMPTS_EXHAUSTED,
    )
