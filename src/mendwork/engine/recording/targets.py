"""Recording a target: selectors verified against the live page, then proven by Rung 0.

Every candidate selector is resolved inside a consistent snapshot (settle, read, confirm
the DOM did not change). A candidate is kept only when it finds exactly the recorded
element. One that matches several elements is tried inside the target's ancestors, at most
two levels deep, before it is dropped. The fingerprint built from the survivors must then
resolve through Phase 3's own Rung 0 to the same element with a confirmed identity, so a
recorded step is known to replay at the moment it is recorded.

The same derivation fingerprints a verified heal's element, so the version a heal creates carries
selectors made exactly as a recording's are (ADR 0013).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.recording import DroppedSelector, DropReason, SelectorChoice
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import (
    AmbiguousTarget,
    PageNeverStable,
    TargetDrifted,
    TargetNotFound,
)
from mendwork.engine.ports.browser_types import ElementIdentity, ElementRef
from mendwork.engine.ports.element_types import ElementFacts
from mendwork.engine.recording.failures import UnusableReason, unusable
from mendwork.engine.recording.fingerprints import build_fingerprint
from mendwork.engine.recording.selectors import (
    Scope,
    candidate_selectors,
    scope_selectors,
    summarize,
    with_scope,
)
from mendwork.engine.recording.target_context import TargetCaptureContext
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.rung0 import resolve_target


@dataclass(frozen=True, slots=True)
class RecordedTarget:
    """A verified target: its fingerprint, the facts and identity behind it, and the evidence."""

    fingerprint: Fingerprint
    facts: ElementFacts
    identity: ElementIdentity
    choice: SelectorChoice
    dropped: tuple[DroppedSelector, ...]


class TargetRecorder:
    """Verifies and fingerprints one pinned element."""

    def __init__(self, context: TargetCaptureContext) -> None:
        self._context = context
        self._browser = context.browser
        self._scopes: tuple[Scope, ...] | None = None

    async def record(
        self, element: ElementRef, *, deadline: Deadline | None = None
    ) -> RecordedTarget:
        """The element's fingerprint. Raises RecordingUnusable when it cannot be recorded.

        ``deadline`` caps the capture when the caller has less time than a step's timeout.
        """
        context = self._context
        own = Deadline.after(context.timer, context.step_timeout_ms)
        deadline = own if deadline is None else own.earliest(deadline)
        identity = await self._browser.identify(element, confirm=True)
        if identity.confirmed is False:
            raise unusable(
                UnusableReason.IDENTITY_UNCONFIRMED,
                "Playwright could not confirm the role and name computed for this element, so a "
                "replay would stop at it",
                role=identity.role,
                tag=identity.tag,
            )
        try:
            facts = await self._browser.element_facts(element)
        except TargetNotFound as error:
            raise _gone() from error
        candidates = candidate_selectors(facts, identity)
        kept, dropped = await self._verify(element, candidates, deadline)
        if not kept:
            raise unusable(
                UnusableReason.NO_SELECTOR,
                f"no selector finds exactly this {identity.role or facts.tag} and nothing else, "
                "so the step could not be replayed",
                dropped=[f"{item.summary}: {item.reason}" for item in dropped],
            )
        try:
            fingerprint = build_fingerprint(facts, identity, kept)
        except ValidationError as error:
            raise unusable(
                UnusableReason.UNRECORDABLE_TARGET,
                "this element's name is too long or contains characters a workflow cannot store",
                tag=facts.tag,
            ) from error
        choice = await self._prove(element, fingerprint, deadline)
        return RecordedTarget(
            fingerprint=fingerprint,
            facts=facts,
            identity=identity,
            choice=choice,
            dropped=tuple(dropped),
        )

    async def _verify(
        self, element: ElementRef, candidates: Sequence[Selector], deadline: Deadline
    ) -> tuple[list[Selector], list[DroppedSelector]]:
        context = self._context
        while True:
            settling = await self._browser.wait_until_settled(
                quiet_frames=context.settle_quiet_frames,
                timeout_ms=deadline.cap(context.settle_timeout_ms),
            )
            kept, dropped = await self._evaluate(element, candidates)
            epoch = await self._browser.dom_epoch(timeout_ms=deadline.timeout_ms())
            if epoch == settling.epoch:
                return kept, dropped
            if deadline.expired:
                raise unusable(
                    UnusableReason.PAGE_NEVER_STABLE,
                    "the page kept changing while this step was recorded, so its selectors could "
                    "not be verified",
                )

    async def _evaluate(
        self, element: ElementRef, candidates: Sequence[Selector]
    ) -> tuple[list[Selector], list[DroppedSelector]]:
        kept: list[Selector] = []
        dropped: list[DroppedSelector] = []
        for candidate in candidates:
            match = await self._browser.resolve_unique(candidate)
            counts = match.level_counts
            if match.element is not None:
                if await self._same(element, match.element):
                    kept.append(candidate)
                else:
                    dropped.append(_dropped(candidate, DropReason.DIFFERENT_ELEMENT, counts))
            elif counts and counts[-1] > 1:
                scoped = await self._scoped(element, candidate)
                if scoped is None:
                    dropped.append(_dropped(candidate, DropReason.AMBIGUOUS, counts))
                elif scoped not in kept:
                    kept.append(scoped)
            else:
                dropped.append(_dropped(candidate, DropReason.NO_MATCH, counts))
        return kept, dropped

    async def _scoped(self, element: ElementRef, candidate: Selector) -> Selector | None:
        scopes = await self._scope_list(element)
        for scope, distance in scopes:
            first = with_scope(candidate, scope)
            if first is None:
                continue
            match = await self._browser.resolve_unique(first)
            if match.element is not None:
                if await self._same(element, match.element):
                    return first
                continue
            if len(match.level_counts) == 1 and match.level_counts[0] > 1:
                deeper = await self._deeper(element, candidate, scope, distance, scopes)
                if deeper is not None:
                    return deeper
        return None

    async def _deeper(
        self,
        element: ElementRef,
        candidate: Selector,
        scope: Selector,
        distance: int,
        scopes: Sequence[Scope],
    ) -> Selector | None:
        for outer, outer_distance in scopes:
            if outer_distance <= distance:
                continue
            nested = with_scope(scope, outer)
            second = None if nested is None else with_scope(candidate, nested)
            if second is None:
                continue
            match = await self._browser.resolve_unique(second)
            if match.element is not None and await self._same(element, match.element):
                return second
        return None

    async def _scope_list(self, element: ElementRef) -> tuple[Scope, ...]:
        if self._scopes is None:
            ancestors = await self._browser.scope_ancestors(
                element, limit=self._context.scope_ancestors_max
            )
            self._scopes = scope_selectors(ancestors)
        return self._scopes

    async def _same(self, element: ElementRef, found: ElementRef) -> bool:
        try:
            keys = await self._browser.group_identical([element, found])
        finally:
            await self._browser.release([found])
        return keys[1] == keys[0]

    async def _prove(
        self, element: ElementRef, fingerprint: Fingerprint, deadline: Deadline
    ) -> SelectorChoice:
        context = self._context
        try:
            resolved = await resolve_target(
                self._browser,
                fingerprint,
                deadline=deadline,
                settle_timeout_ms=context.settle_timeout_ms,
                quiet_frames=context.settle_quiet_frames,
                scrubber=context.scrubber,
            )
        except PageNeverStable as error:
            raise unusable(
                UnusableReason.PAGE_NEVER_STABLE,
                "the page kept changing while this step was recorded, so it could not be proven "
                "to replay",
            ) from error
        except TargetDrifted as error:
            raise unusable(
                UnusableReason.IDENTITY_UNCONFIRMED,
                "the recorded element's identity could not be confirmed when the step was "
                "replayed at record time",
                differences=error.context.get("differences"),
            ) from error
        except (AmbiguousTarget, TargetNotFound) as error:
            raise unusable(
                UnusableReason.NO_SELECTOR,
                "the recorded selectors do not resolve to this element when replayed",
                cause=type(error).__name__,
            ) from error
        if not await self._same(element, resolved.element):
            raise unusable(
                UnusableReason.NO_SELECTOR,
                "the recorded selectors resolve to a different element when replayed",
            )
        rank = resolved.evidence.resolved_rank or 0
        return SelectorChoice(
            rank=rank,
            strategy=fingerprint.selectors[rank].strategy,
            total=len(fingerprint.selectors),
        )


def _dropped(selector: Selector, reason: DropReason, counts: tuple[int, ...]) -> DroppedSelector:
    return DroppedSelector(
        strategy=selector.strategy,
        summary=summarize(selector)[:256],
        reason=reason,
        level_counts=counts,
    )


def _gone() -> Exception:
    return unusable(
        UnusableReason.ELEMENT_GONE,
        "the element left the page before its step could be recorded",
    )
