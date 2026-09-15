"""Turning a finished run's verified heals into workflow versions (ADR 0013).

``promote`` runs when a run finishes, before its final record is written:

- ``immediate``: each qualifying heal becomes a child of the latest version, one version per heal,
  in step order;
- ``after_n_successes``: each qualifying heal, and each pending patch a first try verified, counts
  one more succeeded run under an exclusive hold on the workflow's pending patches. A patch that
  reaches the required count becomes a version; patches the page or the workflow left behind are
  removed.

**Ordering guarantee.** A version is published, atomically and without overwriting, before anything
that says it was: the pending patches are replaced after it, and the run's final record after that.
A process that stops in between leaves a valid version and records that do not mention it. The next
promotion finds the latest step already carrying the healed target and reports ``already_applied``,
so the patch is neither lost nor published twice, and a pending patch left behind no longer matches
its step and is removed.

A store that cannot be read or written never changes a run's outcome: its heals are reported as not
saved, with the reason.
"""

from collections.abc import Mapping

import structlog

from mendwork.engine.domain.changes import HealChange, Promotion
from mendwork.engine.domain.enums import PromotionPolicy, VerificationStrength
from mendwork.engine.domain.heals import HealedRung
from mendwork.engine.domain.identifiers import StepId, WorkflowId
from mendwork.engine.domain.lineage import heal_version
from mendwork.engine.domain.patches import (
    PatchOutcome,
    PatchResult,
    PendingPatch,
    SuccessKind,
    pending_id,
    step_digest,
)
from mendwork.engine.domain.runs import Run
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import VersionConflict, WorkflowValidationError
from mendwork.engine.patching.config import PatchingConfig
from mendwork.engine.patching.eligibility import RunPatchFacts, VerifiedHeal, run_patch_facts
from mendwork.engine.patching.heal_changes import heal_change
from mendwork.engine.patching.promotion import counted, first_tries, started
from mendwork.engine.patching.rebase import Placed, Placement, place, step_named
from mendwork.engine.patching.sources import STORE_ERRORS, stored_history
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.pending_patches import PendingPatches
from mendwork.engine.ports.workflow_store import WorkflowStore
from mendwork.engine.replay.pending_first_try import FirstTry
from mendwork.engine.safety.heal_policy import verification_strength

_SETTLED = frozenset(
    {
        PatchResult.PUBLISHED,
        PatchResult.ALREADY_APPLIED,
        PatchResult.STALE,
        PatchResult.PREVIOUSLY_ROLLED_BACK,
        PatchResult.REFUSED,
    }
)
"""Outcomes after which a pending patch has nothing left to wait for."""
_NO_VERSIONS = "the workflow store has no versions of this workflow"


class Patcher:
    """Saves one tenant's verified heals as workflow versions."""

    def __init__(
        self,
        *,
        store: WorkflowStore,
        pending: PendingPatches,
        clock: Clock,
        config: PatchingConfig,
    ) -> None:
        self._store = store
        self._pending = pending
        self._clock = clock
        self._config = config
        self._log = structlog.stdlib.get_logger("mendwork.patching")

    async def first_tries(self, workflow: WorkflowVersion) -> Mapping[StepId, tuple[FirstTry, ...]]:
        """The pending targets a run of this version tries before healing; none when unreadable."""
        if self._config.promotion is PromotionPolicy.IMMEDIATE:
            return {}
        try:
            patches = await self._pending.read(workflow.workflow_id)
        except STORE_ERRORS as error:
            self._log.warning(
                "pending_patches_unreadable",
                workflow_id=workflow.workflow_id,
                error_type=type(error).__name__,
            )
            return {}
        return first_tries(workflow, patches)

    async def promote(self, run: Run, workflow: WorkflowVersion) -> tuple[PatchOutcome, ...]:
        """What came of every verified heal and pending patch of a finished run."""
        facts = run_patch_facts(run, workflow)
        outcomes = list(facts.refused)
        immediate = self._config.promotion is PromotionPolicy.IMMEDIATE
        if not facts.heals and (immediate or not (facts.first_tries or facts.recorded)):
            return tuple(outcomes)
        try:
            if immediate:
                outcomes.extend(await self._immediately(run, workflow, facts))
            else:
                outcomes.extend(await self._after_successes(run, workflow, facts))
        except STORE_ERRORS as error:
            self._log.warning(
                "patches_not_saved", run_id=run.run_id, error_type=type(error).__name__
            )
            outcomes.extend(_unavailable(heal, error) for heal in facts.heals)
        return tuple(outcomes)

    async def _immediately(
        self, run: Run, workflow: WorkflowVersion, facts: RunPatchFacts
    ) -> list[PatchOutcome]:
        promotion = Promotion(policy=PromotionPolicy.IMMEDIATE, runs=(run.run_id,))
        outcomes: list[PatchOutcome] = []
        for heal in facts.heals:
            change = self._change(heal, run, promotion)
            if isinstance(change, PatchOutcome):
                outcomes.append(change)
                continue
            outcomes.append(await self._publish(workflow.workflow_id, heal.step, change))
        return outcomes

    @staticmethod
    def _change(heal: VerifiedHeal, run: Run, promotion: Promotion) -> HealChange | PatchOutcome:
        """The heal's change record, or why no version could record it."""
        try:
            return heal_change(heal, run, promotion)
        except ValueError as error:
            # pydantic's ValidationError is a ValueError: the heal would not describe a real change,
            # such as an element fingerprinted exactly as it was recorded.
            detail = str(error).splitlines()[-1].strip() or "the heal is not a change"
            return _heal_outcome(heal, PatchResult.REFUSED, None, detail)

    async def _after_successes(
        self, run: Run, workflow: WorkflowVersion, facts: RunPatchFacts
    ) -> list[PatchOutcome]:
        now = self._clock.now()
        outcomes: list[PatchOutcome] = []
        async with self._pending.hold(workflow.workflow_id) as hold:
            history = await self._history(workflow.workflow_id)
            latest = history[-1] if history else None
            patches = {patch.id: patch for patch in hold.patches}
            for patch in list(patches.values()):
                retired = _retired(patch, latest, workflow, facts)
                if retired is not None:
                    del patches[patch.id]
                    outcomes.append(retired)
            touched: list[str] = []
            for success in facts.first_tries:
                existing = patches.get(success.pending_id)
                if existing is not None:
                    patches[existing.id] = counted(existing, run.run_id, now, SuccessKind.FIRST_TRY)
                    touched.append(existing.id)
            for heal in facts.heals:
                if not history:
                    outcomes.append(_heal_outcome(heal, PatchResult.STALE, None, _NO_VERSIONS))
                    continue
                placed = place(history, heal.step, heal.found)
                if placed.placement is not Placement.PUBLISH:
                    outcomes.append(
                        _placed_outcome(heal.step.id, heal.rung, _strength(heal), placed)
                    )
                    continue
                key = pending_id(heal.step.id, step_digest(heal.step), heal.found)
                existing = patches.get(key)
                if existing is None:
                    promotion = Promotion(
                        policy=PromotionPolicy.AFTER_N_SUCCESSES, runs=(run.run_id,)
                    )
                    change = self._change(heal, run, promotion)
                    if isinstance(change, PatchOutcome):
                        outcomes.append(change)
                        continue
                    patches[key] = started(workflow.workflow_id, heal.step, change, run.run_id, now)
                else:
                    patches[key] = counted(existing, run.run_id, now, SuccessKind.HEALED)
                touched.append(key)
            for key in dict.fromkeys(touched):
                outcome = await self._settle_pending(workflow.workflow_id, patches[key], latest)
                if outcome.result in _SETTLED:
                    del patches[key]
                outcomes.append(outcome)
            await hold.replace(tuple(patches.values()))
        return outcomes

    async def _settle_pending(
        self, workflow_id: WorkflowId, patch: PendingPatch, latest: WorkflowVersion | None
    ) -> PatchOutcome:
        required = self._config.successes_required
        count = len(patch.successes)
        base = step_named(latest, patch.step_id) if latest is not None else None
        if count < required or base is None:
            return PatchOutcome(
                step_id=patch.step_id,
                result=PatchResult.PENDING,
                rung=patch.change.rung,
                strength=patch.change.strength,
                successes=count,
                required=required,
                pending_id=patch.id,
            )
        promotion = Promotion(
            policy=PromotionPolicy.AFTER_N_SUCCESSES,
            runs=tuple(success.run_id for success in patch.successes),
        )
        change = patch.change.model_copy(update={"promotion": promotion})
        outcome = await self._publish(workflow_id, base, change)
        return outcome.model_copy(
            update={"successes": count, "required": required, "pending_id": patch.id}
        )

    async def _publish(
        self, workflow_id: WorkflowId, base_step: Step, change: HealChange
    ) -> PatchOutcome:
        """Publish the heal as a child of the latest version, while it still has the exact step."""
        for _ in range(self._config.publish_attempts):
            history = await self._history(workflow_id)
            if not history:
                return _change_outcome(change, PatchResult.STALE, None, _NO_VERSIONS)
            placed = place(history, base_step, change.new_target)
            if placed.placement is not Placement.PUBLISH:
                return _placed_outcome(change.step_id, change.rung, change.strength, placed)
            try:
                child = heal_version(history[-1], change, clock=self._clock)
            except WorkflowValidationError as error:
                return _change_outcome(change, PatchResult.REFUSED, None, error.message)
            try:
                await self._store.publish(child)
            except VersionConflict:
                self._log.info(
                    "heal_publish_conflict", workflow_id=workflow_id, step_id=change.step_id
                )
                continue
            self._log.info(
                "heal_published",
                workflow_id=workflow_id,
                version=child.version,
                step_id=change.step_id,
                rung=change.rung,
            )
            return _change_outcome(change, PatchResult.PUBLISHED, child.version, None)
        return _change_outcome(
            change,
            PatchResult.CONFLICT,
            None,
            f"other versions were published first, {self._config.publish_attempts} times",
        )

    async def _history(self, workflow_id: WorkflowId) -> tuple[WorkflowVersion, ...]:
        return await stored_history(self._store, workflow_id)


def _retired(
    patch: PendingPatch,
    latest: WorkflowVersion | None,
    workflow: WorkflowVersion,
    facts: RunPatchFacts,
) -> PatchOutcome | None:
    """Why a pending patch is removed before this run counts anything, or None to keep it."""
    version = latest.version if latest is not None else None
    current = step_named(latest, patch.step_id) if latest is not None else None
    if current is None or step_digest(current) != patch.base_step_sha256:
        applied = current is not None and step_target(current) == patch.change.new_target
        detail = (
            f"v{version} already targets this element"
            if applied
            else "the step changed since the heal was verified"
        )
        return _discarded(patch, version, detail)
    ran = step_named(workflow, patch.step_id)
    on_recorded = any(step.id == patch.step_id for step in facts.recorded)
    if on_recorded and ran is not None and step_digest(ran) == patch.base_step_sha256:
        return _discarded(patch, version, "the recorded element was found again")
    return None


def _discarded(patch: PendingPatch, version: int | None, detail: str) -> PatchOutcome:
    return PatchOutcome(
        step_id=patch.step_id,
        result=PatchResult.DISCARDED,
        version=version,
        rung=patch.change.rung,
        strength=patch.change.strength,
        successes=len(patch.successes),
        pending_id=patch.id,
        detail=detail,
    )


def _placed_outcome(
    step_id: StepId, rung: HealedRung, strength: VerificationStrength, placed: Placed
) -> PatchOutcome:
    version = placed.version
    if placed.placement is Placement.ALREADY_APPLIED:
        result, detail = PatchResult.ALREADY_APPLIED, f"v{version} already targets this element"
    elif placed.placement is Placement.PREVIOUSLY_ROLLED_BACK:
        result = PatchResult.PREVIOUSLY_ROLLED_BACK
        detail = f"v{version} rolled back this exact heal, so it is not saved again automatically"
    else:
        result, detail = PatchResult.STALE, f"the step changed by v{version}"
    return PatchOutcome(
        step_id=step_id,
        result=result,
        version=version,
        rung=rung,
        strength=strength,
        detail=detail,
    )


def _change_outcome(
    change: HealChange, result: PatchResult, version: int | None, detail: str | None
) -> PatchOutcome:
    return PatchOutcome(
        step_id=change.step_id,
        result=result,
        version=version,
        rung=change.rung,
        strength=change.strength,
        detail=detail,
    )


def _heal_outcome(
    heal: VerifiedHeal, result: PatchResult, version: int | None, detail: str
) -> PatchOutcome:
    return PatchOutcome(
        step_id=heal.step.id,
        result=result,
        version=version,
        rung=heal.rung,
        strength=_strength(heal),
        detail=detail,
    )


def _strength(heal: VerifiedHeal) -> VerificationStrength:
    return verification_strength(heal.step.checkpoints)


def _unavailable(heal: VerifiedHeal, error: Exception) -> PatchOutcome:
    message = error.message if isinstance(error, STORE_ERRORS) else str(error)
    return _heal_outcome(
        heal, PatchResult.STORE_UNAVAILABLE, None, f"{type(error).__name__}: {message}"
    )
