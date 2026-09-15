"""Rung 3: a model chooses among the candidates Rung 2 could not decide between, or none.

The model is a constrained chooser. It is shown numbered descriptions of eligible candidates
and may answer one number or null; it never writes a selector and never sees the page. Its
answer is a proposal:

1. nothing is asked when no candidate is eligible, when the best eligible candidate has a
   look-alike, or when a gate that does not depend on the choice already stops the step;
2. every call, a repair call included, is first reserved against the run's and the day's
   budgets;
3. a reply in the wrong shape gets one repair call; a second such reply, a provider that
   cannot answer, null, or a number that is not on the list abstains;
4. the pick is read again from the page and must pass every safety rule on what it is now,
   and the rules only a model's pick faces (``pick_rules``: it keeps some of its recorded
   context, and a recorded identifier when the step's checkpoints are weak); it must still
   read exactly as the model was shown it, have no look-alike, be confirmed by Playwright, and
   sit on a page that has not changed since Rung 2 read it;
5. an accepted pick then faces the same gates, action, and checkpoints as any heal.

The model's confidence is recorded and never read by a decision. Every element but an
accepted pick is released before returning.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from mendwork.engine.domain.heals import (
    AbstentionReason,
    HealAttemptReport,
    RejectionReason,
    RungOutcome,
    SafetyRejection,
)
from mendwork.engine.domain.model_evidence import (
    BudgetScope,
    BudgetStop,
    ModelCall,
    ModelCallOutcome,
    ModelCallPurpose,
    ModelChoiceEvidence,
    ModelUsage,
    ShownCandidate,
)
from mendwork.engine.errors import (
    BudgetExceeded,
    MendworkError,
    ModelOutputInvalid,
    ProviderError,
    TargetNotFound,
)
from mendwork.engine.healing.candidates import SignatureMatch, found_kind, recorded_kind
from mendwork.engine.healing.checks import safety_rejection
from mendwork.engine.healing.choice import on_the_list
from mendwork.engine.healing.context import (
    AcceptedHeal,
    ClimbRequest,
    LadderContext,
    scored_candidate,
)
from mendwork.engine.healing.eligibility import Eligible, eligibility, has_look_alike
from mendwork.engine.healing.gates import before_asking
from mendwork.engine.healing.model_rung import ModelChooser
from mendwork.engine.healing.pick_rules import context_rejection, verification_rejection
from mendwork.engine.healing.prompt import (
    PROMPT_VERSION,
    describe_candidate,
    render_messages,
    repair_messages,
    response_schema,
)
from mendwork.engine.healing.rung2 import Rung2Decline
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.ports.model_types import ChatMessage, ChoiceRequest, ChoiceResult
from mendwork.engine.replay.identity import same_identity
from mendwork.engine.safety.heal_kinds import compare_kinds


@dataclass(frozen=True, slots=True)
class Rung3Result:
    """Rung 3's report, and its accepted heal or why it abstained."""

    report: HealAttemptReport
    accepted: AcceptedHeal | None = None
    abstention: AbstentionReason | None = None
    """None when no model was asked for a reason Rung 2's own abstention already explains."""


@dataclass(frozen=True, slots=True)
class _Stop:
    outcome: RungOutcome
    abstention: AbstentionReason


async def run_rung3(
    context: LadderContext, request: ClimbRequest, declined: Rung2Decline
) -> Rung3Result:
    """Ask the model to choose among Rung 2's eligible candidates, and judge its choice.

    A request that reuses an earlier pick judges it again without asking, so it needs no model.
    """
    chooser = context.chooser
    if chooser is None and request.reuse is None:
        raise MendworkError("Rung 3 needs a configured model", step_id=request.step.id)
    return await _Rung3(context, request, declined, chooser).run()


class _Rung3:
    """One Rung 3 decision: what was shown, what was answered, and the verdict."""

    def __init__(
        self,
        context: LadderContext,
        request: ClimbRequest,
        declined: Rung2Decline,
        chooser: ModelChooser | None,
    ) -> None:
        self._context = context
        self._request = request
        self._declined = declined
        self._configured = chooser
        scrubber = context.scrubber
        self._judged = eligibility(
            declined.ranked, lambda item: describe_candidate(item.candidate, scrubber)
        )
        self._sent: tuple[Eligible, ...] = ()
        self._shown: tuple[ShownCandidate, ...] = ()
        self._calls: list[ModelCall] = []
        self._answer: ChoiceResult | None = None
        self._unavailable: str | None = None
        self._budget: BudgetStop | None = None

    @property
    def _chooser(self) -> ModelChooser:
        if self._configured is None:
            raise MendworkError("Rung 3 needs a configured model", step_id=self._request.step.id)
        return self._configured

    async def run(self) -> Rung3Result:
        if self._request.reuse is not None:
            return await self._reuse(self._request.reuse)
        eligible = self._judged.eligible
        if not eligible:
            return await self._finish(RungOutcome.NO_ELIGIBLE)
        if has_look_alike(eligible[0], eligible):
            return await self._finish(RungOutcome.LOOK_ALIKES)
        stop = before_asking(
            self._request.step,
            used=self._request.heal_actions_used,
            config=self._context.config,
        )
        if stop is not None:
            return await self._finish(RungOutcome.NOT_ASKED, abstention=stop)
        self._sent = eligible[: self._chooser.config.candidates_k]
        self._shown = tuple(
            ShownCandidate(
                number=number,
                candidate=item.candidate_id,
                description=item.description,
                similarity=item.item.score,
            )
            for number, item in enumerate(self._sent, start=1)
        )
        stopped = await self._ask()
        if stopped is not None:
            return await self._finish(stopped.outcome, abstention=stopped.abstention)
        answer = self._answer
        if answer is None or answer.choice is None:
            return await self._finish(
                RungOutcome.MODEL_ABSTAINED, abstention=AbstentionReason.MODEL_ABSTAINED
            )
        if not on_the_list(answer.choice, len(self._shown)):
            return await self._finish(
                RungOutcome.CHOICE_OUT_OF_RANGE,
                abstention=AbstentionReason.MODEL_CHOICE_OUT_OF_RANGE,
            )
        return await self._judge(self._sent[answer.choice - 1])

    async def _reuse(self, match: SignatureMatch) -> Rung3Result:
        """An earlier pick judged again without a model: the step's own verified pick during a
        restore, or the element a person approved as a resumed run reaches the step."""
        pick = next(
            (item for item in self._judged.eligible if match.matches(item.item.signature)), None
        )
        if pick is None:
            return await self._finish(RungOutcome.NO_ELIGIBLE)
        return await self._judge(pick)

    async def _ask(self) -> _Stop | None:
        context = self._context
        request = self._request
        messages = render_messages(request.step, request.fingerprint, self._shown, context.scrubber)
        try:
            self._answer = await self._call(messages, ModelCallPurpose.CHOOSE)
        except ModelOutputInvalid as invalid:
            repair = repair_messages(
                messages,
                reply=_text(invalid.context.get("excerpt")),
                problem=_text(invalid.context.get("problem")),
                scrubber=context.scrubber,
            )
        except (ProviderError, BudgetExceeded) as error:
            return self._stopped(error)
        else:
            return None
        try:
            self._answer = await self._call(repair, ModelCallPurpose.REPAIR)
        except ModelOutputInvalid:
            return _Stop(RungOutcome.OUTPUT_INVALID, AbstentionReason.MODEL_OUTPUT_INVALID)
        except (ProviderError, BudgetExceeded) as error:
            return self._stopped(error)
        return None

    async def _call(
        self, messages: Sequence[ChatMessage], purpose: ModelCallPurpose
    ) -> ChoiceResult:
        chooser = self._chooser
        await chooser.budget.reserve()
        timeout_ms = self._request.deadline.cap(chooser.config.timeout_ms)
        choice_request = ChoiceRequest(
            prompt_version=PROMPT_VERSION,
            messages=tuple(messages),
            response_schema=response_schema(len(self._shown)),
            shown=self._shown,
            timeout_ms=timeout_ms,
        )
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                result = await chooser.model.choose_candidate(choice_request)
        except TimeoutError as timed_out:
            usage = self._usage(None, latency_ms=timeout_ms)
            self._record(purpose, ModelCallOutcome.UNAVAILABLE, "no answer in time", usage)
            raise ProviderError(
                f"the model did not answer within {timeout_ms} ms", reason="timeout"
            ) from timed_out
        except ModelOutputInvalid as invalid:
            problem = _text(invalid.context.get("problem"))
            usage = self._usage(invalid.context.get("usage"))
            self._record(purpose, ModelCallOutcome.INVALID_OUTPUT, problem, usage)
            raise
        except ProviderError as error:
            reason = _text(error.context.get("reason"))
            usage = self._usage(error.context.get("usage"))
            self._record(purpose, ModelCallOutcome.UNAVAILABLE, reason, usage)
            raise
        self._record(purpose, ModelCallOutcome.ANSWERED, None, result.usage)
        return result

    def _record(
        self,
        purpose: ModelCallPurpose,
        outcome: ModelCallOutcome,
        problem: str | None,
        usage: ModelUsage,
    ) -> None:
        self._chooser.budget.record(usage)
        self._calls.append(
            ModelCall(purpose=purpose, outcome=outcome, problem=problem or None, usage=usage)
        )

    def _usage(self, reported: object, *, latency_ms: int = 0) -> ModelUsage:
        if isinstance(reported, ModelUsage):
            return reported
        config = self._chooser.config
        return ModelUsage(
            provider=config.provider, model=config.model, latency_ms=latency_ms, http_attempts=0
        )

    def _stopped(self, error: ProviderError | BudgetExceeded) -> _Stop:
        scrubber = self._context.scrubber
        if isinstance(error, BudgetExceeded):
            context = error.context
            limit = context.get("limit")
            resets_at = context.get("resets_at")
            self._budget = BudgetStop(
                scope=BudgetScope.RUN if context.get("scope") == "run" else BudgetScope.DAY,
                limit=limit if isinstance(limit, int) else 0,
                resets_at=datetime.fromisoformat(resets_at) if isinstance(resets_at, str) else None,
                detail=scrubber.scrub_text(error.message),
            )
            return _Stop(RungOutcome.BUDGET_EXHAUSTED, AbstentionReason.MODEL_BUDGET_EXHAUSTED)
        self._unavailable = scrubber.scrub_text(error.message)
        return _Stop(RungOutcome.MODEL_UNAVAILABLE, AbstentionReason.MODEL_UNAVAILABLE)

    async def _judge(self, pick: Eligible) -> Rung3Result:
        """Hold the pick to every rule on what it is now, on the page the ranking was read on."""
        context = self._context
        request = self._request
        if has_look_alike(pick, self._judged.eligible):
            return await self._refuse(
                pick,
                RejectionReason.LOOK_ALIKE,
                "another candidate reads exactly the same, so a choice between them means nothing",
            )
        element = pick.item.candidate.element
        try:
            identity = await context.browser.identify(element, confirm=True)
            facts = await context.browser.element_facts(element)
        except TargetNotFound:
            return await self._refuse(
                pick, RejectionReason.UNCONFIRMED_IDENTITY, "it is no longer on the page"
            )
        current = LiveCandidate(element=element, identity=identity, facts=facts)
        rejection = safety_rejection(
            request.step, request.fingerprint, current, context.config.vocabulary
        )
        if rejection is not None:
            return await self._refuse(pick, rejection.reason, rejection.detail)
        vetoed = context_rejection(
            request.fingerprint, current, context.config
        ) or verification_rejection(request.step, request.fingerprint, current)
        if vetoed is not None:
            return await self._refuse(pick, vetoed.reason, vetoed.detail)
        unchanged = same_identity(identity, pick.item.candidate.identity) and (
            describe_candidate(current, context.scrubber) == pick.description
        )
        if not unchanged:
            return await self._refuse(
                pick, RejectionReason.UNCONFIRMED_IDENTITY, "it changed after it was chosen"
            )
        if identity.confirmed is False:
            return await self._refuse(
                pick,
                RejectionReason.UNCONFIRMED_IDENTITY,
                "Playwright could not confirm the role and name computed for it",
            )
        epoch = await context.browser.dom_epoch(timeout_ms=request.deadline.timeout_ms())
        if epoch != self._declined.epoch:
            return await self._finish(
                RungOutcome.PAGE_NEVER_STABLE,
                abstention=AbstentionReason.PAGE_NEVER_STABLE,
                pick=pick,
            )
        report = self._report(RungOutcome.RESOLVED, pick=pick, accepted=True)
        await self._release(keep=pick)
        return Rung3Result(
            report,
            accepted=AcceptedHeal(rung=3, scored=pick.item, identity=identity, report=report),
        )

    async def _refuse(self, pick: Eligible, reason: RejectionReason, detail: str) -> Rung3Result:
        refused = Eligible(
            pick.item.rejected(SafetyRejection(reason=reason, detail=detail)),
            pick.candidate_id,
            pick.description,
        )
        return await self._finish(
            RungOutcome.CHOICE_REFUSED,
            abstention=AbstentionReason.MODEL_CHOICE_REFUSED,
            pick=refused,
        )

    async def _finish(
        self,
        outcome: RungOutcome,
        *,
        abstention: AbstentionReason | None = None,
        pick: Eligible | None = None,
    ) -> Rung3Result:
        report = self._report(outcome, pick=pick, accepted=False)
        await self._release(keep=None)
        return Rung3Result(report, abstention=abstention)

    async def _release(self, keep: Eligible | None) -> None:
        kept = keep.item.candidate.element if keep is not None else None
        await self._context.browser.release(
            [
                item.candidate.element
                for item in self._declined.ranked
                if item.candidate.element != kept
            ]
        )

    def _report(
        self, outcome: RungOutcome, *, pick: Eligible | None, accepted: bool
    ) -> HealAttemptReport:
        scrubber = self._context.scrubber
        listed = self._sent or ((pick,) if pick is not None else ())
        candidates = tuple(
            scored_candidate(
                pick.item
                if pick is not None and item.candidate_id == pick.candidate_id
                else item.item,
                item.candidate_id,
                scrubber,
            )
            for item in listed
        )
        answer = self._answer
        eligible = self._judged.eligible
        kind_change = (
            compare_kinds(
                recorded_kind(self._request.fingerprint), found_kind(pick.item.candidate)
            ).change
            if pick is not None and accepted
            else None
        )
        return HealAttemptReport(
            rung=3,
            attempt=self._request.attempt,
            outcome=outcome,
            candidates=candidates,
            considered=len(eligible),
            on_page=self._declined.report.on_page,
            chosen=pick.candidate_id if pick is not None and accepted else None,
            score=pick.item.score if pick is not None else None,
            kind_change=kind_change,
            model=ModelChoiceEvidence(
                prompt_version=PROMPT_VERSION,
                shown=self._shown,
                not_shown=max(0, len(eligible) - len(self._sent)) if self._sent else 0,
                ineligible=self._judged.ineligible,
                calls=tuple(self._calls),
                choice=answer.choice if answer is not None else None,
                confidence=answer.confidence if answer is not None else None,
                reason=scrubber.scrub_text(answer.reason) if answer is not None else None,
                unavailable=self._unavailable,
                budget=self._budget,
            ),
        )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
