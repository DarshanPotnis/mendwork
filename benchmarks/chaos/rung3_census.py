"""Census of every candidate a model was shown in Rung 3's evaluation, with ground truth.

It exists to choose, from data, the bar a model's pick must clear on a step whose checkpoints are
weak (``url_matches`` or ``field_has_value`` alone), where a look-alike passes verification
(ADR 0010). Every list a model is shown is recorded with each line's live element, then every line
is scored: whether it really was the target, whether its name or label shares wording with the
recording, which identity attributes survived (``href`` excluded), whether the context veto refuses
it, and the step's verification strength. Each candidate bar is then counted two ways: real
targets it would refuse (the cost) and wrong elements it would still let through (the exposure).

The lists come from the ground-truth model (which follows the right path through each run) and
the adversarial model (which explores the lists shown after wrong picks), over the known and
held-out seeds and the fixture suite's cases.

    uv run python -m benchmarks.chaos.rung3_census --json census.json
"""

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, TextIO

from playwright.async_api import async_playwright

from benchmarks.chaos.heal_cases import build_cases, load_workflows, run_case
from benchmarks.chaos.heal_pairs import ABSTAIN_TABLE_PATH, PORTAL_ROOT, load_table
from benchmarks.chaos.models import Asked, ModelMode
from benchmarks.chaos.rung3_eval import cases_for, run_seed
from benchmarks.chaos.workflow_targets import load_workflow_targets
from mendwork.apps.cli.wiring import healing_config
from mendwork.apps.portal.server import PortalServer
from mendwork.engine.domain.enums import VerificationStrength
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.healing.config import HealingConfig
from mendwork.engine.healing.features import label_similarity, name_similarity
from mendwork.engine.healing.pick_rules import (
    IDENTIFIERS,
    context_rejection,
    surviving_identity_attributes,
)
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.safety.heal_policy import verification_strength
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.observability import configure_logging
from mendwork.settings import Settings

MODES: Final[tuple[ModelMode, ...]] = ("oracle", "adversarial")


@dataclass(frozen=True, slots=True)
class Line:
    """One line of one list a model was shown."""

    source: str
    step_id: str
    strength: str
    name: str | None
    target: bool
    wording: bool
    attributes: tuple[str, ...]
    context_vetoed: bool


Bar = Callable[[Line], bool]
BARS: Final[Mapping[str, Bar]] = {
    "none": lambda line: True,
    "wording": lambda line: line.wording,
    "any identity attribute": lambda line: bool(line.attributes),
    "an identifier (id, name, test id)": lambda line: bool(IDENTIFIERS & set(line.attributes)),
    "wording and any identity attribute": lambda line: line.wording and bool(line.attributes),
    "wording and an identifier": lambda line: (
        line.wording and bool(IDENTIFIERS & set(line.attributes))
    ),
}


def shares_wording(
    fingerprint: Fingerprint, candidate: LiveCandidate, config: HealingConfig
) -> bool:
    """Whether the candidate's name or label has anything in common with the recording's."""
    floor = config.name_similarity_floor
    return (
        name_similarity(fingerprint, candidate, floor) > 0
        or label_similarity(fingerprint, candidate, floor) > 0
    )


def lines_of(source: str, asked: Sequence[Asked], steps: Mapping[str, Step]) -> list[Line]:
    """Every line of every list, scored with the rules' own functions."""
    config = healing_config(Settings(_env_file=None))
    found: list[Line] = []
    for call in asked:
        step = steps.get(call.step_id or "")
        fingerprint = step_target(step) if step is not None else None
        if step is None or fingerprint is None:
            continue
        strength = verification_strength(step.checkpoints)
        for item, candidate in zip(call.shown, call.candidates, strict=False):
            if candidate is None:
                continue
            found.append(
                Line(
                    source=source,
                    step_id=step.id,
                    strength=strength.value,
                    name=item.description.name,
                    target=item.number in call.targets,
                    wording=shares_wording(fingerprint, candidate, config),
                    attributes=surviving_identity_attributes(fingerprint, candidate),
                    context_vetoed=context_rejection(fingerprint, candidate, config) is not None,
                )
            )
    return found


async def collect(out: TextIO) -> list[Line]:
    settings = Settings().model_copy(update={"trace_on_failure": False})
    configure_logging(settings, SecretScrubber())
    workflows = load_workflows()
    steps: dict[str, Step] = {
        str(step.id): step for workflow in workflows.values() for step in workflow.version.steps
    }
    download = workflows["download_report"]
    targets = load_workflow_targets("download_report").targets
    seeds = (*cases_for("known"), *cases_for("held_out"))
    suite = build_cases(workflows, load_table(), load_table(ABSTAIN_TABLE_PATH))
    lines: list[Line] = []
    with (
        tempfile.TemporaryDirectory(prefix="mendwork-rung3-census-") as scratch,
        PortalServer(PORTAL_ROOT, host="127.0.0.1", port=0) as server,
    ):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                for mode in MODES:
                    for case in seeds:
                        asked: list[Asked] = []
                        await run_seed(
                            browser, server.url, download.version, targets, case,
                            Path(scratch) / f"{mode}-{case.level}-{case.seed}",
                            settings=settings, model=mode, client=None, asked=asked,
                        )  # fmt: skip
                        lines += lines_of(f"{mode} {case.label}", asked, steps)
                    for heal_case in suite:
                        asked = []
                        await run_case(
                            browser, server.url, heal_case, workflows,
                            Path(scratch) / f"{mode}-{heal_case.id}", model=mode, asked=asked,
                        )  # fmt: skip
                        lines += lines_of(f"{mode} {heal_case.id}", asked, steps)
                    out.write(f"{mode}: {len(lines)} lines so far\n")
                    out.flush()
            finally:
                await browser.close()
    return lines


def distinct(lines: Sequence[Line]) -> list[Line]:
    """Each distinct line once, whichever model's run showed it."""
    seen: dict[tuple[object, ...], Line] = {}
    for line in lines:
        case = line.source.split(" ", 1)[1]
        key = (
            case,
            line.step_id,
            line.name,
            line.target,
            line.attributes,
            line.context_vetoed,
            line.wording,
        )
        seen.setdefault(key, line)
    return list(seen.values())


def report(lines: Sequence[Line], out: TextIO) -> None:
    unique = distinct(lines)
    out.write(f"\n{len(unique)} distinct lines shown to a model\n")
    for strength in (VerificationStrength.WEAK.value, VerificationStrength.STRONG.value):
        group = [line for line in unique if line.strength == strength]
        targets = [line for line in group if line.target]
        others = [line for line in group if not line.target]
        out.write(
            f"\n{strength} verification: {len(group)} lines, {len(targets)} real targets, "
            f"{len(others)} wrong elements\n"
        )
        for vetoed in (False, True):
            label = "after the context veto" if vetoed else "without the context veto"
            out.write(f"  {label}:\n")
            for name, bar in BARS.items():
                passing = [
                    line for line in group if bar(line) and not (vetoed and line.context_vetoed)
                ]
                refused = [line for line in targets if line not in passing]
                admitted = [line for line in others if line in passing]
                out.write(
                    f"    {name:<40} refuses {len(refused)} real targets, admits "
                    f"{len(admitted)} wrong elements\n"
                )
                for line in refused:
                    out.write(f"        refused target: {_describe(line)}\n")
                for line in admitted:
                    out.write(f"        admitted wrong: {_describe(line)}\n")


def _describe(line: Line) -> str:
    return (
        f"{line.source} · {line.step_id} · {json.dumps(line.name)} · wording {line.wording} · "
        f"attributes {list(line.attributes)} · context veto {line.context_vetoed}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.chaos.rung3_census", description=__doc__
    )
    parser.add_argument("--json", type=Path, help="write every line as JSON")
    arguments = parser.parse_args(argv)
    lines = asyncio.run(collect(sys.stdout))
    report(lines, sys.stdout)
    if arguments.json is not None:
        arguments.json.write_text(
            json.dumps([asdict(line) for line in lines], indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
