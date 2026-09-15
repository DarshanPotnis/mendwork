"""Pending patches' first tries: a heal waiting to become a version is tried before the ladder.

Under ``after_n_successes`` a verified heal waits as a pending patch (ADR 0013). When a later run's
Rung 0 finds nothing, or finds the recorded selectors agreeing on an element whose identity drifted,
each pending target for the step is resolved once with Rung 0 itself, without waiting for the page
to change, since the recorded target has already waited. A resolved target is acted on exactly as a
Rung 0 target is. Nothing is tried after an ambiguous verdict, where the page has look-alikes and
only the ladder's margin can decide, and nothing is tried before the recorded target, which is
preferred whenever it is on the page.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.identifiers import StepId
from mendwork.engine.errors import (
    InfrastructureError,
    MendworkError,
    TargetDrifted,
    TargetNotFound,
)
from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.config import ReplayConfig
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.emitter import RunEmitter
from mendwork.engine.replay.progress import StepProgress
from mendwork.engine.replay.rung0 import resolve_target
from mendwork.engine.replay.step_actions import ActionTarget
from mendwork.engine.safety.secret_scrub import SecretScrubber


@dataclass(frozen=True, slots=True)
class FirstTry:
    """A pending patch's target, tried once at Rung 0 before a step's heal ladder."""

    patch_id: str
    target: Fingerprint


class FirstTries:
    """One run's pending targets, by step."""

    def __init__(
        self,
        *,
        browser: BrowserPort,
        tries: Mapping[StepId, tuple[FirstTry, ...]],
        emitter: RunEmitter,
        config: ReplayConfig,
        timer: Timer,
        run_deadline: Deadline,
        scrubber: SecretScrubber,
    ) -> None:
        self._browser = browser
        self._tries = tries
        self._emitter = emitter
        self._config = config
        self._timer = timer
        self._run_deadline = run_deadline
        self._scrubber = scrubber

    async def resolve(self, progress: StepProgress, failure: MendworkError) -> ActionTarget | None:
        """A pending target for the step that resolves now, or None to heal as usual."""
        tries = self._tries.get(progress.step.id, ())
        if not tries or not isinstance(failure, TargetNotFound | TargetDrifted):
            return None
        config = self._config
        for item in tries:
            deadline = Deadline.after(self._timer, config.step_timeout_ms).earliest(
                self._run_deadline
            )
            try:
                resolved = await resolve_target(
                    self._browser,
                    item.target,
                    deadline=deadline,
                    settle_timeout_ms=config.settle_timeout_ms,
                    quiet_frames=config.settle_quiet_frames,
                    scrubber=self._scrubber,
                    patient=False,
                )
            except InfrastructureError:
                raise
            except MendworkError:
                continue
            progress.pinned.append(resolved.element)
            evidence = resolved.evidence.model_copy(update={"pending_patch": item.patch_id})
            if not progress.quiet:
                progress.target = evidence
                await self._emitter.target_resolved(progress.index, progress.step.id, evidence)
            return ActionTarget(resolved.element, resolved.identity, resolved.selector)
        return None
