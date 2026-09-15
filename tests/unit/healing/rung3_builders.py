"""A scripted ledger page and a scripted model for Rung 3's tests.

The recorded control is a ledger's export button. ``renamed`` is that button under a new name and
id (below Rung 2's threshold, so a model is asked), ``summary`` another button sharing some
wording, and ``noise`` one sharing nothing but its kind.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from mendwork.adapters.models.fake import Reply
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import HealAttemptReport
from mendwork.engine.domain.model_evidence import ModelChoiceEvidence
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.healing.candidates import CandidateSignature, SignatureMatch
from mendwork.engine.healing.context import ClimbRequest, LadderContext
from mendwork.engine.healing.ladder import ClimbResult, climb
from mendwork.engine.healing.model_rung import ModelChoiceConfig, ModelChooser, ModelRung
from mendwork.engine.ports.element_types import Box
from mendwork.engine.ports.model import ModelPort
from mendwork.engine.ports.model_types import ChoiceRequest
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.reports import TARGET_CONTEXT_KEY
from mendwork.engine.safety.budgets import BudgetLimits
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser
from tests.fakes.clock import FakeClock
from tests.fakes.ledger import InMemoryUsageLedger
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button, step
from tests.unit.replay.builders import healing
from tests.workflows import click_step

RECORDED: Final = export_button(selectors=[EXPORT_TEST_ID.model_dump()])
CHECKED: Final = [{"kind": "text_present", "text": "Ledger exported"}]
NOW: Final = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
FAR: Final = Box(x=0.0, y=0.9, width=0.1, height=0.05)
RENAMED: Final = "Quarterly download"


def export_step(
    fingerprint: Fingerprint = RECORDED,
    *,
    risk: str = "safe",
    checkpoints: object = CHECKED,
) -> Step:
    return step(
        click_step(target=fingerprint.model_dump(mode="json"), risk=risk, checkpoints=checkpoints)
    )


def reply(
    choice: int | None, confidence: float = 0.9, reason: str = "It exports the ledger."
) -> str:
    return json.dumps({"choice": choice, "confidence": confidence, "reason": reason})


def not_found() -> TargetNotFound:
    return TargetNotFound("nothing", reason="no_match", **{TARGET_CONTEXT_KEY: {"selectors": []}})


def renamed(page: FakeBrowser, key: str = "renamed", **facts: object) -> None:
    """The recorded button under a new name and id, keeping its test id: below the threshold."""
    add_element(
        page,
        key,
        RECORDED,
        identity={"name": RENAMED},
        facts={"text": RENAMED, "id": "quarterly-download", **facts},
    )


def summary(page: FakeBrowser, key: str = "summary", *, box: Box = FAR) -> None:
    """Another button sharing some wording and nothing else: eligible, far behind."""
    add_element(
        page,
        key,
        RECORDED,
        identity={"name": "Export summary"},
        facts={
            "text": "Export summary",
            "id": None,
            "data_testid": None,
            "nearby_text": (),
            "box": box,
        },
    )


def noise(page: FakeBrowser, key: str = "noise") -> None:
    """A button with nothing but its kind in common: never shown to a model."""
    add_element(
        page,
        key,
        RECORDED,
        identity={"name": "Invite teammate"},
        facts={"text": "Invite teammate", "id": None, "data_testid": None, "nearby_text": ()},
    )


def chooser_for(
    model: ModelPort,
    *,
    per_run: int = 4,
    per_day: int = 200,
    k: int = 5,
    timeout_ms: int = 5_000,
    ledger: InMemoryUsageLedger | None = None,
) -> ModelChooser:
    rung = ModelRung(
        model=model,
        config=ModelChoiceConfig(
            candidates_k=k, timeout_ms=timeout_ms, provider="fake", model="scripted"
        ),
        limits=BudgetLimits(per_run=per_run, per_day=per_day),
        ledger=ledger or InMemoryUsageLedger(),
    )
    return rung.for_run(FakeClock(NOW))


async def climb_with(
    page: FakeBrowser,
    chooser: ModelChooser | None,
    *,
    target: Step | None = None,
    used: int = 0,
    reuse: CandidateSignature | None = None,
) -> ClimbResult:
    action = target or export_step()
    fingerprint = step_target(action)
    assert fingerprint is not None
    context = LadderContext(
        browser=page,
        config=healing(),
        settle_timeout_ms=100,
        quiet_frames=2,
        scrubber=SecretScrubber(),
        chooser=chooser,
    )
    request = ClimbRequest(
        step=action,
        fingerprint=fingerprint,
        attempt=1,
        excluded=frozenset(),
        deadline=Deadline.after(page.timer, 30_000),
        heal_actions_used=used,
        reuse=SignatureMatch(reuse) if reuse is not None else None,
    )
    return await climb(context, request, not_found())


def rung3(result: ClimbResult) -> HealAttemptReport:
    return next(report for report in result.reports if report.rung == 3)


def evidence(result: ClimbResult) -> ModelChoiceEvidence:
    model = rung3(result).model
    assert model is not None
    return model


def released_keys(page: FakeBrowser) -> set[str]:
    return {page.key_of(ref) for ref in page.released}


def changes(page: FakeBrowser, key: str, **facts: object) -> Callable[[ChoiceRequest], Reply]:
    """A model that answers 1 while the page changes that element under it."""

    def respond(request: ChoiceRequest) -> Reply:
        element = page.elements[key]
        for field in ("name", "role", "tag", "input_type", "confirmed"):
            if field in facts:
                setattr(element, field, facts[field])
        fact_updates = {
            name: value
            for name, value in facts.items()
            if name not in {"name", "role", "input_type", "confirmed"}
        }
        if "name" in facts:
            fact_updates["text"] = facts["name"]
        page.facts[key] = page.facts[key].model_copy(update=fact_updates)
        return reply(1)

    return respond
