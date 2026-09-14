"""Invariants of Rung 3, whatever a model answers.

- A pick that names a danger word the recording did not have is never accepted, whether the word
  was on the page from the start or appeared while the model was choosing, and whichever number
  the model answers. The prompt's instruction to avoid destructive controls is not what stops it.
- The model's confidence never decides anything: the same answer with any confidence gives the
  same result.
- When Rung 2 accepts, no model is asked.
- The rules only a model's pick faces only ever refuse: without them the same page and answer
  accept whatever they accept, and they never turn an abstention into an accept.
"""

import asyncio
import json
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Final
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from mendwork.adapters.models.fake import FakeModel, Reply
from mendwork.engine.domain.heals import RungOutcome
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.errors import TargetNotFound
from mendwork.engine.healing.context import ClimbRequest, LadderContext
from mendwork.engine.healing.ladder import ClimbResult, climb
from mendwork.engine.healing.model_rung import ModelChoiceConfig, ModelRung
from mendwork.engine.ports.element_types import Box
from mendwork.engine.ports.model_types import ChoiceRequest
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.reports import TARGET_CONTEXT_KEY
from mendwork.engine.safety.budgets import BudgetLimits
from mendwork.engine.safety.heal_policy import introduced_danger
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser
from tests.fakes.clock import FakeClock
from tests.fakes.ledger import InMemoryUsageLedger
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button, step
from tests.unit.recording.builders import VOCABULARY
from tests.unit.replay.builders import browser, healing
from tests.workflows import click_step

RECORDED: Final = export_button(selectors=[EXPORT_TEST_ID.model_dump()])
DANGER: Final = sorted(VOCABULARY.danger_words - VOCABULARY.soft_verbs)
NAMES: Final = ("Quarterly download", "Export summary", "Ledger export file", "Export report")
ACTION: Final = step(click_step(target=RECORDED.model_dump(mode="json"), risk="safe", checkpoints=[
    {"kind": "text_present", "text": "Ledger exported"}
]))  # fmt: skip


def page_with(names: list[str], danger_at: int | None, danger: str) -> FakeBrowser:
    """Candidates like the recorded button, renamed, each at its own position."""
    page = browser()
    for position, name in enumerate(names):
        shown = f"{danger.capitalize()} {name.lower()}" if position == danger_at else name
        add_element(
            page,
            f"k{position}",
            RECORDED,
            identity={"name": shown},
            facts={
                "text": shown,
                "id": f"id-{position}",
                "box": Box(x=0.1 * position, y=0.5, width=0.05, height=0.05),
            },
        )
    return page


def climb_sync(
    page: FakeBrowser, replies: list[Reply] | FakeModel, action: Step = ACTION
) -> tuple[ClimbResult, FakeModel]:
    model = replies if isinstance(replies, FakeModel) else FakeModel(replies)
    chooser = ModelRung(
        model=model,
        config=ModelChoiceConfig(
            candidates_k=5, timeout_ms=5_000, provider="fake", model="scripted"
        ),
        limits=BudgetLimits(per_run=4, per_day=200),
        ledger=InMemoryUsageLedger(),
    ).for_run(FakeClock(datetime(2026, 9, 13, tzinfo=UTC)))
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
    )
    failure = TargetNotFound(
        "nothing", reason="no_match", **{TARGET_CONTEXT_KEY: {"selectors": []}}
    )
    return asyncio.run(climb(context, request, failure)), model


def answer(choice: int | None, confidence: float = 0.9) -> str:
    return json.dumps({"choice": choice, "confidence": confidence, "reason": "It is that one."})


@given(
    names=st.lists(st.sampled_from(NAMES), min_size=1, max_size=4, unique=True),
    danger=st.sampled_from(DANGER),
    target=st.integers(min_value=0, max_value=3),
    choice=st.integers(min_value=1, max_value=4),
    during_call=st.booleans(),
)
def test_a_pick_that_gains_a_danger_word_is_never_accepted(
    names: list[str], danger: str, target: int, choice: int, during_call: bool
) -> None:
    danger_at = target % len(names)
    page = page_with(names, None if during_call else danger_at, danger)

    def respond(request: ChoiceRequest) -> Reply:
        if during_call:
            key = f"k{danger_at}"
            dangerous = f"{danger.capitalize()} {names[danger_at].lower()}"
            page.elements[key].name = dangerous
            page.facts[key] = page.facts[key].model_copy(update={"text": dangerous})
        return answer(choice)

    result, _ = climb_sync(page, FakeModel(respond))

    accepted = result.accepted
    if accepted is not None:
        element = page.elements[page.key_of(accepted.scored.candidate.element)]
        recorded = (RECORDED.accessible_name, RECORDED.text, RECORDED.label_text)
        assert introduced_danger(recorded, (element.name,), VOCABULARY) == ()


@given(
    names=st.lists(st.sampled_from(NAMES), min_size=1, max_size=4, unique=True),
    choice=st.none() | st.integers(min_value=-1, max_value=5),
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_the_models_confidence_never_changes_the_decision(
    names: list[str], choice: int | None, confidence: float
) -> None:
    confident, _ = climb_sync(page_with(names, None, "delete"), [answer(choice, confidence)])
    unsure, _ = climb_sync(page_with(names, None, "delete"), [answer(choice, 0.0)])

    def decision(result: ClimbResult) -> tuple[object, ...]:
        accepted = result.accepted
        return (
            result.abstention,
            None if accepted is None else accepted.scored.signature,
            [(report.rung, report.outcome) for report in result.reports],
        )

    assert decision(confident) == decision(unsure)


@given(
    decoys=st.lists(st.sampled_from(NAMES), max_size=3, unique=True),
    choice=st.none() | st.integers(min_value=1, max_value=4),
)
def test_no_model_is_asked_when_rung2_accepts(decoys: list[str], choice: int | None) -> None:
    page = page_with(decoys, None, "delete")
    add_element(page, "recorded", RECORDED)

    result, model = climb_sync(page, [answer(choice)])

    rung2 = next(report for report in result.reports if report.rung == 2)
    if rung2.outcome is RungOutcome.RESOLVED:
        assert model.requests == []
        assert all(report.rung != 3 for report in result.reports)
    assert (model.requests == []) == all(report.rung != 3 for report in result.reports)


PICK_RULES: Final = ("context_rejection", "verification_rejection")
"""The rules only a model's pick faces, as ``rung3`` names them (ADR 0010)."""
CONTEXTS: Final = ((), ("Quarterly ledger",), ("Main navigation",), ("Quarterly ledger", "Menu"))
CHECKPOINTS: Final = (
    [{"kind": "text_present", "text": "Ledger exported"}],
    [{"kind": "url_matches", "mode": "prefix", "pattern": "https://ledger.example.test/"}],
)


@st.composite
def varied_pages(draw: st.DrawFn) -> list[dict[str, object]]:
    """Up to four renamed buttons, each keeping or losing its context and its identifiers."""
    names = draw(st.lists(st.sampled_from(NAMES), min_size=1, max_size=4, unique=True))
    return [
        {
            "text": name,
            "id": draw(st.sampled_from((f"id-{position}", RECORDED.attributes.id))),
            "data_testid": draw(st.sampled_from((None, RECORDED.attributes.data_testid))),
            "nearby_text": draw(st.sampled_from(CONTEXTS)),
            "box": Box(x=0.1 * position, y=0.5, width=0.05, height=0.05),
        }
        for position, name in enumerate(names)
    ]


def varied_page(facts: list[dict[str, object]]) -> FakeBrowser:
    page = browser()
    for position, fact in enumerate(facts):
        add_element(
            page, f"k{position}", RECORDED, identity={"name": str(fact["text"])}, facts=fact
        )
    return page


@given(
    facts=varied_pages(),
    checkpoints=st.sampled_from(CHECKPOINTS),
    choice=st.none() | st.integers(min_value=0, max_value=5),
)
def test_the_pick_rules_only_ever_refuse(
    facts: list[dict[str, object]], checkpoints: list[dict[str, object]], choice: int | None
) -> None:
    action = step(click_step(target=RECORDED.model_dump(mode="json"), checkpoints=checkpoints))
    with_rules, _ = climb_sync(varied_page(facts), [answer(choice)], action)
    with ExitStack() as stack:
        for rule in PICK_RULES:
            stack.enter_context(patch(f"mendwork.engine.healing.rung3.{rule}", return_value=None))
        without_rules, _ = climb_sync(varied_page(facts), [answer(choice)], action)

    earlier = [report for report in with_rules.reports if report.rung < 3]
    assert earlier == [report for report in without_rules.reports if report.rung < 3]
    if with_rules.accepted is not None:
        assert without_rules.accepted is not None
        assert with_rules.accepted.scored.signature == without_rules.accepted.scored.signature
    if without_rules.accepted is None:
        assert with_rules.accepted is None
        assert with_rules.abstention is not None
