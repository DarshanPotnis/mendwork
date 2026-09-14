"""Rung 3's evaluation models: a ground-truth chooser, an adversarial one, or the configured one.

Benchmark code may read the chaos portal's ground truth; Mendwork never does. The ground-truth
and adversarial choosers are functions behind the product's own FakeModel, so their replies go
through the same strict parsing, budgets, and safety rules as any provider's.

- **Ground truth** answers the number whose description equals the real target's, as the model
  would see it, and null when the step must abstain, when the target is not on the list, or when
  more than one line reads like it. It answers only what a perfect chooser could see.
- **Adversarial** answers, with full confidence, a number that is *not* the real target whenever
  the list allows one, and 1 otherwise. It measures what the rules and checkpoints stop when a
  model is confidently wrong.
- **Recording** wraps any of them, the configured model included, and notes for every call which
  listed lines really were the target at that moment, so an evaluation can say whether a choice
  was right without trusting the run's own verdict.
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal

import httpx

from benchmarks.chaos.ground_truth import StepTracker
from mendwork.adapters.models.fake import FakeModel
from mendwork.adapters.usage_fs.ledger import FileUsageLedger
from mendwork.apps.cli.wiring import budget_limits, model_rung
from mendwork.engine.domain.model_evidence import CandidateDescription, ShownCandidate
from mendwork.engine.healing.model_rung import ModelChoiceConfig, ModelRung
from mendwork.engine.healing.prompt import describe_candidate
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.ports.model import ModelPort
from mendwork.engine.ports.model_types import ChoiceRequest, ChoiceResult
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings

ModelMode = Literal["none", "oracle", "adversarial", "configured"]
MODEL_MODES: Final[tuple[ModelMode, ...]] = ("none", "oracle", "adversarial", "configured")


def truth_description(tracker: StepTracker, step_id: str | None) -> CandidateDescription | None:
    """How a model would see the step's real target at its latest scan, if the target was there."""
    truth = tracker.truths.get(step_id) if step_id is not None else None
    return None if truth is None else describe_candidate(truth, SecretScrubber())


def truth_numbers(
    shown: tuple[ShownCandidate, ...], truth: CandidateDescription | None
) -> tuple[int, ...]:
    """The numbered lines that read exactly like the real target."""
    if truth is None:
        return ()
    return tuple(item.number for item in shown if item.description == truth)


def _answer(choice: int | None, reason: str) -> str:
    return json.dumps({"choice": choice, "confidence": 1.0, "reason": reason})


@dataclass(frozen=True, slots=True)
class GroundTruthChooser:
    """Answers from ground truth, and only what the numbered list could tell a perfect chooser."""

    tracker: StepTracker
    abstain_steps: frozenset[str]

    def __call__(self, request: ChoiceRequest) -> str:
        step_id = self.tracker.current
        if step_id is None or step_id in self.abstain_steps:
            return _answer(None, "Ground truth: this step must abstain.")
        numbers = truth_numbers(request.shown, truth_description(self.tracker, step_id))
        if len(numbers) != 1:
            return _answer(None, "Ground truth: the target is not exactly one listed line.")
        return _answer(numbers[0], "Ground truth: this line is the target.")


@dataclass(frozen=True, slots=True)
class AdversarialChooser:
    """Confidently picks a line that is not the target whenever there is one."""

    tracker: StepTracker

    def __call__(self, request: ChoiceRequest) -> str:
        truth = truth_description(self.tracker, self.tracker.current)
        wrong = [item.number for item in request.shown if item.description != truth]
        return _answer(wrong[0] if wrong else 1, "Adversarial: certain.")


@dataclass(frozen=True, slots=True)
class Asked:
    """One model call: the step, the list shown, which lines really were the target, and the live
    element behind each line (None when the scan held no single element reading like it)."""

    step_id: str | None
    shown: tuple[ShownCandidate, ...]
    targets: tuple[int, ...]
    candidates: tuple[LiveCandidate | None, ...] = ()


@dataclass(frozen=True, slots=True)
class TruthRecordingModel:
    """A model whose every call is noted against ground truth, then passed on unchanged."""

    inner: ModelPort
    tracker: StepTracker
    log: list[Asked]

    async def choose_candidate(self, request: ChoiceRequest) -> ChoiceResult:
        step_id = self.tracker.current
        truth = truth_description(self.tracker, step_id)
        scanned = self.tracker.scans.get(step_id, ()) if step_id is not None else ()
        described = [(describe_candidate(item, SecretScrubber()), item) for item in scanned]
        candidates = tuple(
            _single([c for d, c in described if d == item.description]) for item in request.shown
        )
        self.log.append(
            Asked(step_id, request.shown, truth_numbers(request.shown, truth), candidates)
        )
        return await self.inner.choose_candidate(request)


def _single(found: list[LiveCandidate]) -> LiveCandidate | None:
    return found[0] if len(found) == 1 else None


def model_rung_for(
    mode: ModelMode,
    settings: Settings,
    *,
    tracker: StepTracker,
    abstain_steps: frozenset[str],
    ledger_directory: Path,
    client: httpx.AsyncClient | None,
    asked: list[Asked] | None = None,
) -> ModelRung | None:
    """Rung 3 for an evaluation mode, with a daily ledger that belongs to the evaluation."""
    rung = _rung(mode, settings, tracker, abstain_steps, ledger_directory, client)
    if rung is None or asked is None:
        return rung
    return replace(rung, model=TruthRecordingModel(rung.model, tracker, asked))


def _rung(
    mode: ModelMode,
    settings: Settings,
    tracker: StepTracker,
    abstain_steps: frozenset[str],
    ledger_directory: Path,
    client: httpx.AsyncClient | None,
) -> ModelRung | None:
    if mode == "none":
        return None
    if mode == "configured":
        if client is None:
            raise ValueError(
                "the configured model needs an HTTP client; set MENDWORK_MODEL_PROVIDER"
            )
        return model_rung(settings, client=client, ledger_directory=ledger_directory)
    chooser: GroundTruthChooser | AdversarialChooser
    if mode == "oracle":
        chooser = GroundTruthChooser(tracker, abstain_steps)
    else:
        chooser = AdversarialChooser(tracker)
    return ModelRung(
        model=FakeModel(chooser, provider=mode, model="ground-truth"),
        config=ModelChoiceConfig(
            candidates_k=settings.model_candidates_k,
            timeout_ms=settings.model_timeout_ms,
            provider=mode,
            model="ground-truth",
        ),
        limits=budget_limits(settings),
        ledger=FileUsageLedger(ledger_directory),
    )
