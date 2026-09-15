"""Rung 3's evaluation: complete download_report runs on chosen chaos seeds, every action checked
against ground truth, and what Rung 3 did at each step it was consulted on.

Sets:

- ``known``: the six Rung 2 stops Phase 5 found (level 3 seeds 3 and 15; level 5 seeds 3, 9, 10,
  and 11);
- ``held_out``: the ten seeds in rung3_holdout.json, chosen from a Rung 2-only survey before any
  model was run, to check that a model generalizes rather than fits the known cases.

For every Rung 3 decision it reports whether the real target was on the list the model saw and
whether the model chose it, independently of the run's own verdict; for every run, the actions
checked, wrong actions, false successes (a step that passed its checkpoints on the wrong
element), model calls, tokens, latency, and cost. With ``--repeat`` every case runs several
times and any case whose outcome varies is reported, never retried.

    uv run python -m benchmarks.chaos.rung3_eval --model oracle --set known
    MENDWORK_MODEL_PROVIDER=ollama MENDWORK_MODEL_NAME=qwen3:4b-instruct-2507-q4_K_M \\
        uv run python -m benchmarks.chaos.rung3_eval --model configured --set held_out --repeat 3
"""

import argparse
import asyncio
import json
import math
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, TextIO

import httpx
from playwright.async_api import Browser, async_playwright

from benchmarks.chaos.heal_cases import run_with_ground_truth
from benchmarks.chaos.heal_pairs import PORTAL_ROOT
from benchmarks.chaos.models import MODEL_MODES, Asked, ModelMode
from benchmarks.chaos.rung0_seeds import (
    ABSTAIN_EXPECTED,
    DEMO_EMAIL,
    WORKFLOW_ID,
    WORKFLOW_PATH,
    SeedOutcome,
    attribute,
    checked_actions,
    false_successes,
    ground_truth,
    wrong_actions,
)
from benchmarks.chaos.workflow_targets import load_workflow_targets
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.wiring import model_client
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.domain.model_evidence import ModelUsageTotals
from mendwork.engine.domain.runs import Run
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.observability import configure_logging
from mendwork.settings import Settings

KNOWN_CASES: Final = ((3, 3), (3, 15), (5, 3), (5, 9), (5, 10), (5, 11))
HOLDOUT_PATH: Final = Path(__file__).resolve().parent / "rung3_holdout.json"
SETS: Final = ("known", "held_out")


@dataclass(frozen=True, slots=True)
class SeedCase:
    """One seed at one level."""

    level: int
    seed: int

    @property
    def label(self) -> str:
        return f"L{self.level} s{self.seed}"


@dataclass(frozen=True, slots=True)
class Rung3Decision:
    """What Rung 3 did at one heal attempt, checked against ground truth."""

    step_id: str
    attempt: int
    rung2_outcome: str
    outcome: str
    shown: int
    calls: int
    target_listed: bool | None
    """Whether the real target was one of the lines shown; None when no model was asked."""
    choice: int | None
    chose_target: bool | None
    """Whether the chosen line was the real target; None when nothing was chosen."""
    reason: str | None
    verification: str
    latencies_ms: tuple[int, ...]
    refused: str | None = None
    """The rule that refused the model's choice, when one did."""


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One complete run of one seed, with Rung 3's decisions and ground truth."""

    case: SeedCase
    status: str
    stopped_at: str | None
    stop_reason: str | None
    healed: tuple[str, ...]
    decisions: tuple[Rung3Decision, ...]
    abstain_targets: tuple[str, ...]
    actions_checked: int
    wrong_actions: tuple[str, ...]
    false_successes: tuple[str, ...]
    model_usage: ModelUsageTotals

    def summary(self) -> str:
        """One line to compare between repeats: outcome, stop, heals, and each choice."""
        choices = ", ".join(f"{d.step_id}:{d.outcome}:{d.choice}" for d in self.decisions) or "-"
        return (
            f"{self.case.label}: {self.status} stop={self.stopped_at}:{self.stop_reason} "
            f"healed={','.join(self.healed) or '-'} rung3={choices} wrong={len(self.wrong_actions)}"
        )


def cases_for(name: str) -> tuple[SeedCase, ...]:
    """The cases of a set, in a stable order."""
    if name == "known":
        return tuple(SeedCase(level, seed) for level, seed in KNOWN_CASES)
    holdout = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    return tuple(
        SeedCase(int(level), int(seed))
        for level, seeds in sorted(holdout["levels"].items())
        for seed in seeds
    )


def decisions(run: Run, asked: Sequence[Asked]) -> tuple[Rung3Decision, ...]:
    """Every Rung 3 report of a run, paired with the ground truth noted when the model was asked."""
    remaining = list(asked)
    found: list[Rung3Decision] = []
    for step in run.steps:
        heal = step.heal
        if heal is None:
            continue
        previous = "-"
        for report in heal.attempts:
            if report.rung == 2:
                previous = report.outcome.value
            evidence = report.model
            if report.rung != 3 or evidence is None:
                continue
            mine = [
                item
                for item in remaining
                if item.step_id == step.step_id and item.shown == evidence.shown
            ][: len(evidence.calls)]
            for item in mine:
                remaining.remove(item)
            targets = mine[0].targets if mine else ()
            asked_model = bool(evidence.calls)
            found.append(
                Rung3Decision(
                    step_id=step.step_id,
                    attempt=report.attempt,
                    rung2_outcome=previous,
                    outcome=report.outcome.value,
                    shown=len(evidence.shown),
                    calls=len(evidence.calls),
                    target_listed=bool(targets) if asked_model else None,
                    choice=evidence.choice,
                    chose_target=(
                        evidence.choice in targets
                        if asked_model and evidence.choice is not None
                        else None
                    ),
                    reason=evidence.reason,
                    verification=report.verification.value,
                    refused=next(
                        (
                            candidate.rejection.reason.value
                            for candidate in report.candidates
                            if candidate.rejection is not None
                        ),
                        None,
                    ),
                    latencies_ms=tuple(call.usage.latency_ms for call in evidence.calls),
                )
            )
    return tuple(found)


async def run_seed(
    browser: Browser,
    portal: str,
    workflow: WorkflowVersion,
    targets: Mapping[str, str],
    case: SeedCase,
    directory: Path,
    *,
    settings: Settings,
    model: ModelMode,
    client: httpx.AsyncClient | None,
    asked: list[Asked] | None = None,
) -> CaseResult:
    """Replay download_report on one seed with ground truth, and read what Rung 3 did.

    ``asked``, when given, receives every model call with its ground truth.
    """
    applied = await ground_truth(browser, portal, case.seed, case.level)
    abstain_keys = {
        mutation.target_key
        for mutations in applied.values()
        for mutation in mutations
        if mutation.category == ABSTAIN_EXPECTED and mutation.target_key is not None
    }
    abstain_steps = frozenset(step for step, key in targets.items() if key in abstain_keys)
    if asked is None:
        asked = []
    inputs = {
        "portal_url": f"{portal}index.html?seed={case.seed}&level={case.level}",
        "account_email": DEMO_EMAIL,
    }
    replay = await run_with_ground_truth(
        browser,
        workflow,
        inputs,
        targets,
        directory,
        settings=settings,
        signed_in=False,
        model=model,
        abstain_steps=abstain_steps,
        client=client,
        asked=asked,
    )
    run = replay.run
    outcome = SeedOutcome(
        case.seed,
        run,
        applied,
        attribute(run, targets, applied),
        replay.checks,
        replay.wrong_actions,
    )
    failed = run.failed_step
    error = run.error
    return CaseResult(
        case=case,
        status=run.status.value,
        stopped_at=failed.step_id if failed is not None else None,
        stop_reason=str(error.context.get("reason", error.type)) if error is not None else None,
        healed=tuple(
            f"{step.step_id}@r{step.heal.healed_rung}"
            for step in run.steps
            if step.heal is not None and step.heal.healed_rung is not None
        ),
        decisions=decisions(run, asked),
        abstain_targets=tuple(sorted(abstain_keys)),
        actions_checked=len(checked_actions(outcome)),
        wrong_actions=tuple(item.detail for item in wrong_actions(outcome)),
        false_successes=tuple(item.detail for item in false_successes(outcome)),
        model_usage=run.model_usage,
    )


def _refusal(decision: Rung3Decision) -> str:
    return f" ({decision.refused})" if decision.refused is not None else ""


def describe(result: CaseResult) -> list[str]:
    """A case's report: its run, each Rung 3 decision, ground truth, and cost."""
    usage = result.model_usage
    stop = (
        "succeeded"
        if result.stopped_at is None
        else f"stopped at {result.stopped_at} ({result.stop_reason})"
    )
    lines = [
        f"{result.case.label}: {stop} | healed {', '.join(result.healed) or 'none'} | "
        f"{result.actions_checked} actions checked, {len(result.wrong_actions)} wrong, "
        f"{len(result.false_successes)} false successes | {usage.calls} model calls, "
        f"{usage.input_tokens} tokens in, {usage.output_tokens} out, "
        f"{usage.latency_ms / 1000:.1f} s, est. ${usage.estimated_cost_usd}"
    ]
    for decision in result.decisions:
        listed = {True: "yes", False: "no", None: "-"}[decision.target_listed]
        chose = {True: "the target", False: "NOT the target", None: "nothing"}[
            decision.chose_target
        ]
        lines.append(
            f"    rung 3 at {decision.step_id} (attempt {decision.attempt}, after rung 2 "
            f"{decision.rung2_outcome}): {decision.outcome}{_refusal(decision)}; shown "
            f"{decision.shown}, target "
            f"listed {listed}; chose {decision.choice} = {chose}; verification "
            f"{decision.verification}; reason {json.dumps(decision.reason)}"
        )
    lines.extend(f"    WRONG: {detail}" for detail in result.wrong_actions)
    lines.extend(f"    FALSE SUCCESS: {detail}" for detail in result.false_successes)
    return lines


def totals(results: Sequence[CaseResult]) -> list[str]:
    """What Rung 3 added across every run: resolutions, wrong choices, abstentions, and cost.

    A false success is any wrong action in a step that finally succeeded, even one whose own
    checkpoints failed and was undone; a wrong pick that passed verification is a model choice of a
    line that was not the target whose checkpoints then passed, the case weak checkpoints allow."""
    every = [decision for result in results for decision in result.decisions]
    asked = [decision for decision in every if decision.calls]
    resolved = [decision for decision in every if decision.outcome == "resolved"]
    right = [decision for decision in resolved if decision.chose_target]
    wrong_choices = [decision for decision in asked if decision.chose_target is False]
    verified_wrong = [
        decision
        for decision in every
        if decision.chose_target is False and decision.verification == "passed"
    ]
    abstained = [decision for decision in every if decision.outcome != "resolved"]
    latencies = sorted(ms for decision in every for ms in decision.latencies_ms)
    calls = sum(result.model_usage.calls for result in results)
    runs = len(results) or 1
    by_outcome: dict[str, int] = {}
    for decision in abstained:
        by_outcome[decision.outcome] = by_outcome.get(decision.outcome, 0) + 1
    cost = sum((result.model_usage.estimated_cost_usd for result in results), start=0)
    return [
        f"runs {len(results)} · succeeded {sum(1 for r in results if r.status == 'succeeded')}",
        f"rung 3 decisions {len(every)} · model asked {len(asked)} · resolved {len(resolved)} "
        f"(chose the target {len(right)}) · model chose a wrong line {len(wrong_choices)} · "
        f"abstained {len(abstained)} {by_outcome or ''}",
        f"target listed when asked: {sum(1 for d in asked if d.target_listed)} of {len(asked)}",
        f"actions checked {sum(r.actions_checked for r in results)} · wrong actions "
        f"{sum(len(r.wrong_actions) for r in results)} · false successes "
        f"{sum(len(r.false_successes) for r in results)} · wrong picks that passed verification "
        f"{len(verified_wrong)}",
        f"model calls {calls} ({calls / runs:.2f} per run) · latency p50 "
        f"{_percentile(latencies, 0.5)} ms, p95 {_percentile(latencies, 0.95)} ms · est. cost "
        f"${cost} ({cost / runs} per run)",
    ]


def _percentile(values: Sequence[int], fraction: float) -> int | None:
    if not values:
        return None
    return values[max(0, math.ceil(fraction * len(values)) - 1)]


async def evaluate(
    model: ModelMode,
    cases: Sequence[SeedCase],
    repeat: int,
    out: TextIO,
) -> list[list[CaseResult]]:
    """Run every case ``repeat`` times, one run at a time, and report as each finishes."""
    settings = Settings().model_copy(update={"trace_on_failure": False})
    configure_logging(settings, SecretScrubber())
    content = await asyncio.to_thread(WORKFLOW_PATH.read_bytes)
    workflow = WorkflowYamlCodec(max_bytes=settings.workflow_max_bytes).decode(
        content, source=str(WORKFLOW_PATH)
    )
    targets = load_workflow_targets(WORKFLOW_ID).targets
    rounds: list[list[CaseResult]] = []
    with (
        tempfile.TemporaryDirectory(prefix="mendwork-rung3-eval-") as scratch,
        PortalServer(PORTAL_ROOT, host="127.0.0.1", port=0) as server,
    ):
        async with async_playwright() as playwright, model_client(settings) as client:
            browser = await playwright.chromium.launch()
            try:
                for number in range(1, repeat + 1):
                    out.write(f"\nround {number} · model {model}\n")
                    results: list[CaseResult] = []
                    for case in cases:
                        result = await run_seed(
                            browser,
                            server.url,
                            workflow,
                            targets,
                            case,
                            Path(scratch) / f"{number}-{case.level}-{case.seed}",
                            settings=settings,
                            model=model,
                            client=client if model == "configured" else None,
                        )
                        results.append(result)
                        out.write("\n".join(describe(result)) + "\n")
                        out.flush()
                    out.write("\n".join(totals(results)) + "\n")
                    rounds.append(results)
            finally:
                await browser.close()
    return rounds


def varying(rounds: Sequence[Sequence[CaseResult]]) -> list[str]:
    """Cases whose outcome differed between rounds."""
    if len(rounds) < 2:
        return []
    return [
        " | ".join(results[index].summary() for results in rounds)
        for index in range(len(rounds[0]))
        if len({results[index].summary() for results in rounds}) > 1
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.chaos.rung3_eval", description=__doc__
    )
    parser.add_argument("--model", choices=MODEL_MODES, required=True)
    parser.add_argument("--set", choices=(*SETS, "all"), default="known")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", type=Path, help="write every result as JSON")
    arguments = parser.parse_args(argv)
    names = SETS if arguments.set == "all" else (arguments.set,)
    cases = tuple(case for name in names for case in cases_for(name))
    rounds = asyncio.run(evaluate(arguments.model, cases, arguments.repeat, sys.stdout))
    changed = varying(rounds)
    sys.stdout.write(f"\nrepeatability: {len(changed)} of {len(cases)} cases varied\n")
    for line in changed:
        sys.stdout.write(f"  {line}\n")
    if arguments.json is not None:
        document = [
            [
                {**asdict(result), "model_usage": result.model_usage.model_dump(mode="json")}
                for result in results
            ]
            for results in rounds
        ]
        text = json.dumps(document, indent=2, default=str) + "\n"
        arguments.json.write_text(text, encoding="utf-8")
    harmful = any(
        result.wrong_actions or result.false_successes for results in rounds for result in results
    )
    return 1 if harmful or changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
