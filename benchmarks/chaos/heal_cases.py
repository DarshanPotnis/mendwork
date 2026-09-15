"""The heal fixture suite: every mutation that can reach the example workflows' targets.

Cases:

- every heal_expected pair in heal_pairs.json whose target a step of download_report or
  view_order_detail acts on;
- ``cookie_banner`` on every page those workflows act on;
- every abstain_expected pair in abstain_pairs.json on those targets.

Each case replays a segment of a committed workflow: the steps on the mutated page, up to and
including the case's step. The sign-in page is opened by the workflow's own first step, with
``?seed=&only=`` in its URL. Every other page is opened by one inserted navigate step carrying
them, with the portal session seeded by the harness, so each case exercises exactly the page
chaos was applied to. The complete workflows run end to end elsewhere (level 0, and seed 0 at
level 3).

Ground truth comes from ``window.__chaos`` at the moment of every action (see ground_truth).
A heal case must succeed with every action on the real target; an abstain case must abstain at
its step with nothing acted on there. Any wrong action is reported, and fails the suite. With a
model (see models), Rung 3 runs where Rung 2 declines, under the same checks.
"""

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import httpx
from playwright.async_api import Browser
from pydantic import JsonValue

from benchmarks.chaos.ground_truth import (
    DEMO_EMAIL,
    ActionCheck,
    GroundTruthLauncher,
    StepTracker,
)
from benchmarks.chaos.heal_pairs import ABSTAIN, HEAL, Category, HealPairTable
from benchmarks.chaos.local_egress import local_policy
from benchmarks.chaos.models import Asked, ModelMode, model_rung_for
from benchmarks.chaos.workflow_targets import load_workflow_targets
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.browser_playwright.launcher import PlaywrightLauncher
from mendwork.adapters.secrets_env.naming import secret_variable_name
from mendwork.adapters.system.resolver import SystemHostResolver
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.apps.cli.wiring import build_replayer, egress_enforcement, session_options
from mendwork.engine.domain.documents import parse_workflow_document, workflow_document
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.events import RunEvent
from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.domain.patches import WorkflowSource
from mendwork.engine.domain.runs import Run, RunStatus, StepResult, StepStatus
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.patching.patcher import Patcher
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings

WORKFLOW_IDS: Final = ("download_report", "view_order_detail")
EXAMPLES_DIR: Final = Path(__file__).resolve().parents[2] / "workflows" / "examples"
PAGE_FILES: Final[Mapping[str, str]] = {
    "login": "index.html",
    "dashboard": "dashboard.html",
    "reports": "reports.html",
    "orders": "orders.html",
}
PAGE_MUTATION: Final = "cookie_banner"
# The portal's fictional, documented demo credential.
DEMO_PASSWORD: Final = "harbor-demo"  # noqa: S105
NOT_FOUND_STEP_TIMEOUT_MS: Final = 1_500
"""A removed control leaves Rung 0 nothing to wait for; a short step timeout keeps it quick."""
CHAOS_STEP_ID: Final = "open_mutated_page"
CHAOS_INPUT: Final = "chaos_url"
SUITE_CONCURRENCY: Final = 4
USAGE_DIRECTORY: Final = "usage"


class Verdict(StrEnum):
    """What a case's step came to."""

    RESOLVED = "resolved"
    ABSTAINED = "abstained"
    WRONG = "wrong"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExampleWorkflow:
    """A committed example workflow and its ground-truth targets."""

    version: WorkflowVersion
    targets: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class HealCase:
    """One mutation on one target (or on the page), and the workflow segment that reaches it."""

    page: str
    mutation: str
    target: str | None
    seed: int
    category: Category
    workflow_id: str
    step_ids: tuple[str, ...]
    target_step: str

    @property
    def id(self) -> str:
        return f"{self.mutation}-{self.target or self.page}"

    @property
    def expected(self) -> Verdict:
        return Verdict.RESOLVED if self.category == HEAL else Verdict.ABSTAINED


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """What a case did: its verdict, the rung that found its step's target, any wrong action, and
    how many model calls it made."""

    case: HealCase
    verdict: Verdict
    rung: int | None
    reason: str | None
    wrong: tuple[str, ...]
    detail: str
    model_calls: int = 0
    captured: bool | None = None
    """Whether a heal's element was fingerprinted, so it could become a version (ADR 0013); None
    when the case's step was not healed."""

    def summary(self) -> str:
        """One line a person can compare between runs."""
        rung = "-" if self.rung is None else str(self.rung)
        return (
            f"{self.case.id}: {self.verdict} rung={rung} reason={self.reason or '-'} "
            f"calls={self.model_calls}"
        )


@dataclass(frozen=True, slots=True)
class GroundTruthRun:
    """A replay with every action checked against ground truth."""

    run: Run
    events: tuple[RunEvent, ...]
    checks: tuple[ActionCheck, ...]
    wrong_actions: tuple[str, ...]
    run_directory: Path
    tracker: StepTracker

    def step(self, step_id: str) -> StepResult:
        return next(step for step in self.run.steps if step.step_id == step_id)

    def wrong(self) -> tuple[str, ...]:
        """Every action that reached the wrong element, and every wrong action the page recorded."""
        misses = tuple(
            f"{check.action} at step {check.step_id} did not reach {check.target_key}"
            for check in self.checks
            if check.correct is False
        )
        return misses + tuple(
            f"the page recorded a wrong action: {label}" for label in self.wrong_actions
        )


def load_workflows() -> dict[str, ExampleWorkflow]:
    """The example workflows the suite replays, with their ground-truth targets."""
    codec = WorkflowYamlCodec(max_bytes=1 << 20)
    workflows: dict[str, ExampleWorkflow] = {}
    for workflow_id in WORKFLOW_IDS:
        path = EXAMPLES_DIR / f"{workflow_id}.yaml"
        version = codec.decode(path.read_bytes(), source=str(path))
        workflows[workflow_id] = ExampleWorkflow(
            version, load_workflow_targets(workflow_id).targets
        )
    return workflows


def build_cases(
    workflows: Mapping[str, ExampleWorkflow], heal: HealPairTable, abstain: HealPairTable
) -> tuple[HealCase, ...]:
    """Every case the example workflows can reach, in a stable order."""
    owner: dict[str, tuple[str, str]] = {}
    for workflow_id, workflow in workflows.items():
        for step_id, key in workflow.targets.items():
            owner.setdefault(key, (workflow_id, step_id))
    cases: list[HealCase] = []
    for category, table in ((HEAL, heal), (ABSTAIN, abstain)):
        for pair in table.pairs:
            found = owner.get(pair.target)
            if found is None:
                continue
            workflow_id, step_id = found
            cases.append(
                HealCase(
                    page=pair.page,
                    mutation=pair.mutation,
                    target=pair.target,
                    seed=pair.seed,
                    category=category,
                    workflow_id=workflow_id,
                    step_ids=_segment(workflows[workflow_id], pair.page, step_id),
                    target_step=step_id,
                )
            )
    for page in PAGE_FILES:
        last = _last_step_on(workflows, page)
        if last is not None:
            workflow_id, step_id = last
            cases.append(
                HealCase(
                    page=page,
                    mutation=PAGE_MUTATION,
                    target=None,
                    seed=0,
                    category=HEAL,
                    workflow_id=workflow_id,
                    step_ids=_segment(workflows[workflow_id], page, step_id),
                    target_step=step_id,
                )
            )
    return tuple(sorted(cases, key=lambda case: (case.category, case.mutation, case.page, case.id)))


def case_settings(case: HealCase) -> Settings:
    """Default settings, without failure traces (evidence, not behaviour), and short not-found
    waits."""
    update: dict[str, int | bool] = {"trace_on_failure": False}
    if case.mutation == "remove_target":
        update["step_timeout_ms"] = NOT_FOUND_STEP_TIMEOUT_MS
    return Settings(_env_file=None).model_copy(update=update)


def case_workflow(
    case: HealCase, workflow: ExampleWorkflow, portal_url: str
) -> tuple[WorkflowVersion, dict[str, str]]:
    """The case's segment of the workflow, and the inputs that open its mutated page."""
    document = workflow_document(workflow.version)
    by_id = {str(step["id"]): step for step in _objects(document["steps"])}
    steps: list[JsonValue] = [by_id[step_id] for step_id in case.step_ids]
    chaos_url = f"{portal_url}{PAGE_FILES[case.page]}?seed={case.seed}&only={case.mutation}"
    values = {"portal_url": chaos_url, "account_email": DEMO_EMAIL, CHAOS_INPUT: chaos_url}
    if case.page != "login":
        steps.insert(
            0,
            {
                "id": CHAOS_STEP_ID,
                "intent": "Open the page chaos is applied to",
                "action": "navigate",
                "risk": "safe",
                "value": {"kind": "input", "name": CHAOS_INPUT},
            },
        )
    used = _references(steps)
    inputs: list[JsonValue] = [
        declaration for declaration in _objects(document["inputs"]) if declaration["name"] in used
    ]
    if case.page != "login":
        inputs.append({"name": CHAOS_INPUT, "kind": "url"})
    document["steps"] = steps
    document["inputs"] = inputs
    document["secrets"] = [name for name in _strings(document["secrets"]) if name in used]
    version = parse_workflow_document(document)
    declared = {declaration.name for declaration in version.inputs}
    return version, {name: value for name, value in values.items() if name in declared}


async def run_with_ground_truth(
    browser: Browser,
    workflow: WorkflowVersion,
    inputs: Mapping[str, str],
    targets: Mapping[str, str],
    directory: Path,
    *,
    settings: Settings,
    signed_in: bool,
    model: ModelMode = "none",
    abstain_steps: frozenset[str] = frozenset(),
    client: httpx.AsyncClient | None = None,
    asked: list[Asked] | None = None,
    patcher: Patcher | None = None,
    source: WorkflowSource | None = None,
) -> GroundTruthRun:
    """Replay a workflow as ``mendwork run`` would, checking every action against ground truth.

    ``asked``, when given, collects every model call with the lines that really were the target.
    ``patcher`` and ``source``, when given, save the run's verified heals as versions and try
    pending patches first, exactly as ``mendwork run`` does (ADR 0013).
    """
    tracker = StepTracker()
    checks: list[ActionCheck] = []
    wrong_actions: list[str] = []
    # Every run targets the local portal by loopback address, so no name is ever resolved.
    resolver = SystemHostResolver()
    inner = await PlaywrightLauncher.create(
        browser, session_options(settings), egress_enforcement(settings, resolver)
    )
    launcher = GroundTruthLauncher(inner, targets, tracker, checks, wrong_actions, signed_in)
    artifacts = LocalArtifactStore(directory)
    replayer = build_replayer(
        settings,
        launcher=launcher,
        artifacts=artifacts,
        events=tracker,
        environ={secret_variable_name(SecretName("portal_password")): DEMO_PASSWORD},
        egress=local_policy(inputs.values()),
        resolver=resolver,
        # The only secret is the chaos portal's demo password, which the portal publishes in its
        # own JavaScript, so a benchmark needs no scrubber shared with its log pipeline.
        scrubber=SecretScrubber(),
        model=model_rung_for(
            model,
            settings,
            tracker=tracker,
            abstain_steps=abstain_steps,
            ledger_directory=directory / USAGE_DIRECTORY,
            client=client,
            asked=asked,
        ),
        patcher=patcher,
    )
    run = await replayer.run(workflow, inputs, source=source)
    return GroundTruthRun(
        run,
        tuple(tracker.events),
        tuple(checks),
        tuple(wrong_actions),
        artifacts.run_directory(run.run_id),
        tracker,
    )


async def run_case(
    browser: Browser,
    portal_url: str,
    case: HealCase,
    workflows: Mapping[str, ExampleWorkflow],
    directory: Path,
    *,
    model: ModelMode = "none",
    client: httpx.AsyncClient | None = None,
    asked: list[Asked] | None = None,
) -> CaseOutcome:
    """Replay one case and classify what happened."""
    workflow = workflows[case.workflow_id]
    version, inputs = case_workflow(case, workflow, portal_url)
    replay = await run_with_ground_truth(
        browser,
        version,
        inputs,
        workflow.targets,
        directory,
        settings=case_settings(case),
        signed_in=case.page != "login",
        model=model,
        abstain_steps=frozenset({case.target_step}) if case.category == ABSTAIN else frozenset(),
        client=client,
        asked=asked,
    )
    return classify(case, replay)


async def run_cases(
    browser: Browser,
    portal_url: str,
    cases: Sequence[HealCase],
    workflows: Mapping[str, ExampleWorkflow],
    directory: Path,
    *,
    concurrency: int = SUITE_CONCURRENCY,
    model: ModelMode = "none",
    client: httpx.AsyncClient | None = None,
) -> dict[str, CaseOutcome]:
    """Every case, each in its own browser context, at most ``concurrency`` at a time."""
    limit = asyncio.Semaphore(concurrency)

    async def bounded(case: HealCase) -> CaseOutcome:
        async with limit:
            return await run_case(
                browser,
                portal_url,
                case,
                workflows,
                directory / case.id,
                model=model,
                client=client,
            )

    outcomes = await asyncio.gather(*(bounded(case) for case in cases))
    return {outcome.case.id: outcome for outcome in outcomes}


def classify(case: HealCase, replay: GroundTruthRun) -> CaseOutcome:
    """A case's verdict: wrong beats everything, then resolved, abstained, or failed."""
    step = replay.step(case.target_step)
    wrong = replay.wrong()
    if case.category == ABSTAIN:
        wrong += tuple(
            f"{check.action} at step {check.step_id}, which must abstain"
            for check in replay.checks
            if check.step_id == case.target_step
        )
    heal = step.heal
    rung: int | None = heal.healed_rung if heal is not None else None
    if rung is None and step.status is StepStatus.SUCCEEDED:
        rung = 0
    error = step.error
    reason = str(error.context.get("reason")) if error is not None else None
    detail = f"{error.type}: {error.message}" if error is not None else step.status.value
    if wrong:
        verdict = Verdict.WRONG
    elif replay.run.status is RunStatus.SUCCEEDED:
        verdict = Verdict.RESOLVED
    elif _abstained_cleanly(replay.run, step):
        verdict = Verdict.ABSTAINED
    else:
        verdict = Verdict.FAILED
    healed = heal is not None and heal.healed_rung is not None
    captured = (step.found is not None and step.found.fingerprint is not None) if healed else None
    return CaseOutcome(
        case,
        verdict,
        rung,
        reason,
        wrong,
        detail,
        model_calls=replay.run.model_usage.calls,
        captured=captured,
    )


def render_table(outcomes: Mapping[str, CaseOutcome]) -> str:
    """Per mutation: cases, resolved, abstained, wrong, failed, the rungs that resolved them, and
    model calls."""
    by_mutation: dict[tuple[str, str], list[CaseOutcome]] = {}
    for outcome in outcomes.values():
        by_mutation.setdefault((outcome.case.category, outcome.case.mutation), []).append(outcome)
    header = (
        "mutation", "category", "cases", "resolved", "abstained", "wrong", "failed", "rungs",
        "calls",
    )  # fmt: skip
    rows = [header]
    for (category, mutation), group in sorted(by_mutation.items()):
        verdicts = Counter(outcome.verdict for outcome in group)
        rungs = Counter(outcome.rung for outcome in group if outcome.verdict is Verdict.RESOLVED)
        rows.append(
            (
                mutation,
                category.removesuffix("_expected"),
                str(len(group)),
                str(verdicts[Verdict.RESOLVED]),
                str(verdicts[Verdict.ABSTAINED]),
                str(verdicts[Verdict.WRONG]),
                str(verdicts[Verdict.FAILED]),
                " ".join(f"r{rung} x{count}" for rung, count in sorted(rungs.items())) or "-",
                str(sum(outcome.model_calls for outcome in group)),
            )
        )
    total = Counter(outcome.verdict for outcome in outcomes.values())
    rows.append(
        (
            "total",
            "",
            str(len(outcomes)),
            str(total[Verdict.RESOLVED]),
            str(total[Verdict.ABSTAINED]),
            str(total[Verdict.WRONG]),
            str(total[Verdict.FAILED]),
            "",
            str(sum(outcome.model_calls for outcome in outcomes.values())),
        )
    )
    widths = [max(len(row[column]) for row in rows) for column in range(len(header))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    )


def unresolved_heals(outcomes: Mapping[str, CaseOutcome]) -> list[CaseOutcome]:
    """heal_expected cases the ladder did not resolve."""
    return [
        outcome
        for outcome in outcomes.values()
        if outcome.case.category == HEAL and outcome.verdict is not Verdict.RESOLVED
    ]


def _segment(workflow: ExampleWorkflow, page: str, step_id: str) -> tuple[str, ...]:
    """The committed steps on a page, up to and including ``step_id``.

    The sign-in page's segment starts with the workflow's first step, which opens it.
    """
    ids: list[str] = []
    for step in workflow.version.steps:
        key = workflow.targets.get(step.id)
        opens_sign_in = page == "login" and not ids and step.action is ActionType.NAVIGATE
        if opens_sign_in or (key is not None and key.split(".", 1)[0] == page):
            ids.append(step.id)
        if step.id == step_id:
            break
    return tuple(ids)


def _last_step_on(workflows: Mapping[str, ExampleWorkflow], page: str) -> tuple[str, str] | None:
    for workflow_id, workflow in workflows.items():
        on_page = [
            step_id for step_id, key in workflow.targets.items() if key.startswith(f"{page}.")
        ]
        if on_page:
            return workflow_id, on_page[-1]
    return None


def _abstained_cleanly(run: Run, step: StepResult) -> bool:
    before = run.steps[: step.index]
    return (
        step.status is StepStatus.FAILED
        and step.error is not None
        and step.error.type == "HealAbstained"
        and not step.action_performed
        and all(earlier.status is StepStatus.SUCCEEDED for earlier in before)
    )


def _objects(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        raise TypeError("expected a list in the workflow document")
    return [item for item in value if isinstance(item, dict)]


def _strings(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("expected a list in the workflow document")
    return [item for item in value if isinstance(item, str)]


def _references(steps: Sequence[JsonValue]) -> set[str]:
    """Every input and secret name the steps' values refer to."""
    names: set[str] = set()
    for step in steps:
        value = step.get("value") if isinstance(step, dict) else None
        if isinstance(value, dict) and value.get("kind") in {"input", "secret"}:
            names.add(str(value.get("name")))
    return names
