"""Rung 3 on a scripted page with a scripted model: when it asks, how it reads the answer, and
every rule its choice must still pass.

Nothing here comes from the chaos portal: a ledger's export button, an invoice's button, and a
vault passcode field.
"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Final

import pytest

from mendwork.adapters.models.fake import FakeModel, Reply
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import (
    AbstentionReason,
    RejectionReason,
    RungOutcome,
)
from mendwork.engine.domain.model_evidence import (
    BudgetScope,
    ModelCallOutcome,
    ModelCallPurpose,
)
from mendwork.engine.domain.steps import Step
from mendwork.engine.errors import ProviderError
from mendwork.engine.healing.gates import before_asking
from mendwork.engine.healing.prompt import PROMPT_VERSION
from mendwork.engine.ports.element_types import Box
from mendwork.engine.ports.model_types import ChatRole, ChoiceRequest, ChoiceResult
from tests.fakes.ledger import InMemoryUsageLedger
from tests.unit.healing.builders import EXPORT_TEST_ID, add_element, export_button, step
from tests.unit.healing.rung3_builders import (
    FAR,
    RECORDED,
    RENAMED,
    changes,
    chooser_for,
    climb_with,
    evidence,
    export_step,
    noise,
    released_keys,
    renamed,
    reply,
    rung3,
    summary,
)
from tests.unit.replay.builders import browser, healing
from tests.workflows import fill_step

pytestmark = pytest.mark.asyncio


class HangingModel:
    """A provider that never answers."""

    async def choose_candidate(self, request: ChoiceRequest) -> ChoiceResult:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_rung3_never_runs_when_rung2_accepts() -> None:
    page = browser()
    add_element(page, "export", RECORDED)
    model = FakeModel([])

    result = await climb_with(page, chooser_for(model))

    assert result.accepted is not None
    assert result.accepted.rung == 2
    assert [report.rung for report in result.reports] == [0, 1, 2]
    assert model.requests == []


async def test_without_a_model_rung2s_abstention_stands_and_everything_is_released() -> None:
    page = browser()
    renamed(page)
    summary(page)

    result = await climb_with(page, None)

    assert [report.rung for report in result.reports] == [0, 1, 2]
    assert result.abstention is AbstentionReason.BELOW_THRESHOLD
    assert released_keys(page) >= {"renamed", "summary"}


async def test_a_refused_top_candidate_is_never_put_to_a_model() -> None:
    page = browser()
    add_element(page, "delete", RECORDED, identity={"name": "Delete ledger"})
    renamed(page)
    model = FakeModel([reply(1)])

    result = await climb_with(page, chooser_for(model))

    assert result.abstention is AbstentionReason.TOP_REJECTED
    assert [report.rung for report in result.reports] == [0, 1, 2]
    assert model.requests == []


async def test_a_choice_on_the_list_is_accepted_after_every_check_and_the_rest_is_released() -> (
    None
):
    page = browser()
    renamed(page)
    summary(page)
    noise(page)
    ledger = InMemoryUsageLedger()
    model = FakeModel([reply(1)], input_tokens=612, output_tokens=38)
    chooser = chooser_for(model, ledger=ledger)

    result = await climb_with(page, chooser)

    accepted = result.accepted
    assert accepted is not None
    assert (accepted.rung, accepted.identity.name, accepted.identity.confirmed) == (
        3,
        RENAMED,
        True,
    )
    assert page.key_of(accepted.scored.candidate.element) == "renamed"
    assert released_keys(page) >= {"summary", "noise"}
    assert "renamed" not in released_keys(page)
    report = rung3(result)
    assert (report.outcome, report.chosen, report.considered) == (RungOutcome.RESOLVED, "c1", 2)
    assert ledger.calls == {date(2026, 9, 13): 1}
    assert chooser.budget.totals.calls == 1
    assert chooser.budget.totals.input_tokens == 612


async def test_the_request_shows_only_eligible_candidates_as_numbered_descriptions() -> None:
    page = browser()
    renamed(page)
    summary(page)
    noise(page)
    model = FakeModel([reply(None)])

    result = await climb_with(page, chooser_for(model))

    [request] = model.requests
    rung2 = next(report for report in result.reports if report.rung == 2)
    shown = evidence(result).shown
    assert request.shown == shown
    assert [(item.number, item.candidate, item.description.name) for item in shown] == [
        (1, "c1", RENAMED),
        (2, "c3", "Export summary"),
    ]
    scores = {candidate.id: candidate.score for candidate in rung2.candidates}
    assert [item.similarity for item in shown] == [scores["c1"], scores["c3"]]
    assert [message.role for message in request.messages] == [ChatRole.SYSTEM, ChatRole.USER]
    user = request.messages[1].text
    assert f'1. kind: button · name: "{RENAMED}"' in user
    assert "Invite teammate" not in user
    assert request.prompt_version == PROMPT_VERSION
    assert request.response_schema["properties"] == {
        "choice": {"type": ["integer", "null"], "minimum": 1, "maximum": 2},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "minLength": 1, "maxLength": 300},
    }
    assert request.timeout_ms == 5_000
    assert (evidence(result).ineligible, evidence(result).not_shown) == (1, 0)


async def test_only_the_best_k_eligible_candidates_are_shown() -> None:
    page = browser()
    renamed(page)
    summary(page)
    model = FakeModel([reply(None)])

    result = await climb_with(page, chooser_for(model, k=1))

    assert [item.candidate for item in evidence(result).shown] == ["c1"]
    assert evidence(result).not_shown == 1


async def test_null_abstains_as_the_models_own_answer() -> None:
    page = browser()
    renamed(page)
    model = FakeModel([reply(None, reason="None of them exports the ledger.")])

    result = await climb_with(page, chooser_for(model))

    assert result.accepted is None
    assert result.abstention is AbstentionReason.MODEL_ABSTAINED
    assert result.deciding is rung3(result)
    assert (evidence(result).choice, evidence(result).reason) == (
        None,
        "None of them exports the ledger.",
    )
    assert "renamed" in released_keys(page)


@pytest.mark.parametrize("choice", [0, 2, -1])
async def test_a_number_not_on_the_list_abstains_without_a_repair(choice: int) -> None:
    page = browser()
    renamed(page)
    model = FakeModel([reply(choice), reply(1)])

    result = await climb_with(page, chooser_for(model))

    assert result.abstention is AbstentionReason.MODEL_CHOICE_OUT_OF_RANGE
    assert len(model.requests) == 1


async def test_a_reply_in_the_wrong_shape_gets_one_repair_call() -> None:
    page = browser()
    renamed(page)
    unusable = f"Sure! {reply(1)}"
    model = FakeModel([unusable, reply(1)])

    result = await climb_with(page, chooser_for(model))

    assert result.accepted is not None
    first, repair = model.requests
    assert [message.role for message in repair.messages] == [
        ChatRole.SYSTEM,
        ChatRole.USER,
        ChatRole.ASSISTANT,
        ChatRole.USER,
    ]
    assert repair.messages[:2] == first.messages
    assert repair.messages[2].text == unusable
    assert repair.messages[3].text == (
        "Your reply could not be used: it was not exactly one JSON object. Reply with only the "
        "JSON object described above."
    )
    assert [(call.purpose, call.outcome) for call in evidence(result).calls] == [
        (ModelCallPurpose.CHOOSE, ModelCallOutcome.INVALID_OUTPUT),
        (ModelCallPurpose.REPAIR, ModelCallOutcome.ANSWERED),
    ]


async def test_a_second_reply_in_the_wrong_shape_abstains() -> None:
    page = browser()
    renamed(page)
    model = FakeModel(["```json", '{"choice": 1}'])

    result = await climb_with(page, chooser_for(model))

    assert result.abstention is AbstentionReason.MODEL_OUTPUT_INVALID
    assert [call.problem for call in evidence(result).calls] == [
        "it was not exactly one JSON object",
        "it did not have exactly the fields choice, confidence, and reason",
    ]
    assert len(model.requests) == 2


async def test_a_provider_that_cannot_answer_abstains_with_what_it_said() -> None:
    page = browser()
    renamed(page)
    failure = ProviderError("the provider could not be reached (ConnectError)", reason="connection")
    model = FakeModel([failure])

    result = await climb_with(page, chooser_for(model))

    assert result.abstention is AbstentionReason.MODEL_UNAVAILABLE
    assert evidence(result).unavailable == "the provider could not be reached (ConnectError)"
    [call] = evidence(result).calls
    assert (call.outcome, call.problem) == (ModelCallOutcome.UNAVAILABLE, "connection")


async def test_a_model_that_never_answers_is_cut_off_at_the_call_time_limit() -> None:
    page = browser()
    renamed(page)

    result = await climb_with(page, chooser_for(HangingModel(), timeout_ms=5))

    assert result.abstention is AbstentionReason.MODEL_UNAVAILABLE
    assert evidence(result).unavailable == "the model did not answer within 5 ms"
    [call] = evidence(result).calls
    assert (call.outcome, call.problem, call.usage.latency_ms) == (
        ModelCallOutcome.UNAVAILABLE,
        "no answer in time",
        5,
    )


async def test_no_call_is_made_once_the_runs_budget_is_used() -> None:
    page = browser()
    renamed(page)
    model = FakeModel([reply(1)])

    result = await climb_with(page, chooser_for(model, per_run=0))

    assert result.abstention is AbstentionReason.MODEL_BUDGET_EXHAUSTED
    assert model.requests == []
    budget = evidence(result).budget
    assert budget is not None
    assert (budget.scope, budget.limit) == (BudgetScope.RUN, 0)


async def test_no_call_is_made_once_the_days_budget_is_used() -> None:
    page = browser()
    renamed(page)
    ledger = InMemoryUsageLedger()
    ledger.calls[date(2026, 9, 13)] = 200
    model = FakeModel([reply(1)])

    result = await climb_with(page, chooser_for(model, ledger=ledger))

    assert result.abstention is AbstentionReason.MODEL_BUDGET_EXHAUSTED
    budget = evidence(result).budget
    assert budget is not None
    assert (budget.scope, budget.limit, budget.resets_at) == (
        BudgetScope.DAY,
        200,
        datetime(2026, 9, 14, tzinfo=UTC),
    )


async def test_a_repair_call_needs_budget_too() -> None:
    page = browser()
    renamed(page)
    model = FakeModel(["not json", reply(1)])

    result = await climb_with(page, chooser_for(model, per_run=1))

    assert result.abstention is AbstentionReason.MODEL_BUDGET_EXHAUSTED
    assert len(model.requests) == 1
    assert len(evidence(result).calls) == 1


async def test_with_no_eligible_candidate_no_model_is_asked_and_rung2s_reason_stands() -> None:
    page = browser()
    noise(page)
    model = FakeModel([reply(1)])

    result = await climb_with(page, chooser_for(model))

    assert rung3(result).outcome is RungOutcome.NO_ELIGIBLE
    assert result.abstention is AbstentionReason.BELOW_THRESHOLD
    assert result.deciding is not None
    assert result.deciding.rung == 2
    assert model.requests == []


async def test_look_alikes_at_the_top_are_never_put_to_a_model() -> None:
    page = browser()
    renamed(page, "left", box=Box(x=0.1, y=0.3, width=0.1, height=0.05))
    renamed(page, "right", box=Box(x=0.3, y=0.3, width=0.1, height=0.05))
    model = FakeModel([reply(1)])

    result = await climb_with(page, chooser_for(model))

    assert rung3(result).outcome is RungOutcome.LOOK_ALIKES
    assert result.accepted is None
    assert model.requests == []
    assert released_keys(page) >= {"left", "right"}


async def test_a_pick_with_a_look_alike_the_model_was_not_shown_is_refused() -> None:
    page = browser()
    renamed(page)
    summary(page, "summary_a", box=Box(x=0.0, y=0.9, width=0.1, height=0.05))
    summary(page, "summary_b", box=Box(x=0.5, y=0.9, width=0.1, height=0.05))
    model = FakeModel([reply(2)])

    result = await climb_with(page, chooser_for(model, k=2))

    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    refused = [c for c in rung3(result).candidates if c.rejection is not None]
    assert [c.rejection.reason for c in refused if c.rejection] == [RejectionReason.LOOK_ALIKE]


async def test_a_pick_that_now_names_a_destructive_action_is_refused_whatever_the_prompt_said() -> (
    None
):
    page = browser()
    renamed(page)
    model = FakeModel(changes(page, "renamed", name="Delete ledger"))

    result = await climb_with(page, chooser_for(model))

    assert result.accepted is None
    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    [refused] = [c for c in rung3(result).candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert refused.rejection.reason is RejectionReason.DANGER_WORD


async def test_a_pick_that_now_names_another_identifier_is_refused() -> None:
    invoice = export_button(
        accessible_name="Open invoice INV-2231",
        text="Open invoice INV-2231",
        selectors=[EXPORT_TEST_ID.model_dump()],
    )
    page = browser()
    add_element(
        page,
        "invoice",
        invoice,
        identity={"name": "Show invoice INV-2231"},
        facts={
            "text": "Show invoice INV-2231",
            "id": None,
            "data_testid": None,
            "nearby_text": (),
            "box": FAR,
        },
    )
    model = FakeModel(changes(page, "invoice", name="Show invoice INV-7780"))

    result = await climb_with(page, chooser_for(model), target=export_step(invoice))

    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    [refused] = [c for c in rung3(result).candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert refused.rejection.reason is RejectionReason.IDENTIFIER_MISMATCH


async def test_a_pick_that_became_another_kind_of_control_is_refused() -> None:
    page = browser()
    renamed(page)
    model = FakeModel(
        changes(
            page, "renamed", role="checkbox", tag="input", input_type="checkbox", type="checkbox"
        )
    )

    result = await climb_with(page, chooser_for(model))

    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    [refused] = [c for c in rung3(result).candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert refused.rejection.reason is RejectionReason.KIND_CHANGED


PASSCODE: Final = Fingerprint.model_validate(
    {
        "tag": "input",
        "accessible_name": "Vault passcode",
        "label_text": "Vault passcode",
        "attributes": {
            "id": "passcode",
            "name": "passcode",
            "type": "password",
            "autocomplete": "current-password",
        },
        "nearby_text": ["Unlock the vault"],
        "structural_path": "main > form > div > input",
        "bbox": {"x": 0.3, "y": 0.4, "width": 0.3, "height": 0.05},
        "selectors": [{"strategy": "css", "value": "#passcode"}],
    }
)


def passcode_step() -> Step:
    return step(
        fill_step(
            "passcode",
            intent="Fill the 'Vault passcode' field",
            target=PASSCODE.model_dump(mode="json"),
            value={"kind": "secret", "name": "vault_passcode"},
            checkpoints=[{"kind": "field_has_value"}],
        )
    )


async def test_a_credential_field_that_stopped_masking_is_refused() -> None:
    page = browser()
    add_element(
        page,
        "code",
        PASSCODE,
        identity={"name": "Access code"},
        facts={"label_text": "Access code", "id": "code-2", "nearby_text": (), "box": FAR},
    )
    model = FakeModel(changes(page, "code", masked=False))

    result = await climb_with(page, chooser_for(model), target=passcode_step())

    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    [refused] = [c for c in rung3(result).candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert refused.rejection.reason is RejectionReason.CREDENTIAL_MISMATCH


async def test_a_masked_credential_field_the_model_chose_is_accepted() -> None:
    page = browser()
    add_element(
        page,
        "code",
        PASSCODE,
        identity={"name": "Access code"},
        facts={
            "label_text": "Access code",
            "id": "code-2",
            "nearby_text": ("Unlock the vault",),
            "box": FAR,
        },
    )
    model = FakeModel([reply(1)])

    result = await climb_with(page, chooser_for(model), target=passcode_step())

    assert result.accepted is not None
    assert result.accepted.rung == 3


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"confirmed": False}, "Playwright could not confirm the role and name computed for it"),
        (
            {"nearby_text": ("Quarterly ledger", "Archived ledgers")},
            "it changed after it was chosen",
        ),
    ],
)
async def test_a_pick_playwright_cannot_confirm_or_that_changed_is_refused(
    change: Mapping[str, object], detail: str
) -> None:
    page = browser()
    renamed(page)
    model = FakeModel(changes(page, "renamed", **change))

    result = await climb_with(page, chooser_for(model))

    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    [refused] = [c for c in rung3(result).candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert (refused.rejection.reason, refused.rejection.detail) == (
        RejectionReason.UNCONFIRMED_IDENTITY,
        detail,
    )


async def test_a_pick_that_left_the_page_is_refused() -> None:
    page = browser()
    renamed(page)

    def respond(request: ChoiceRequest) -> Reply:
        page.detached.add("renamed")
        return reply(1)

    result = await climb_with(page, chooser_for(FakeModel(respond)))

    assert result.abstention is AbstentionReason.MODEL_CHOICE_REFUSED
    [refused] = [c for c in rung3(result).candidates if c.rejection is not None]
    assert refused.rejection is not None
    assert refused.rejection.detail == "it is no longer on the page"


async def test_a_page_that_changed_while_the_model_chose_is_not_acted_on() -> None:
    page = browser()
    renamed(page)

    def respond(request: ChoiceRequest) -> Reply:
        page.change()
        return reply(1)

    result = await climb_with(page, chooser_for(FakeModel(respond)))

    assert result.accepted is None
    assert result.abstention is AbstentionReason.PAGE_NEVER_STABLE
    assert rung3(result).outcome is RungOutcome.PAGE_NEVER_STABLE


async def test_a_step_that_could_not_prove_a_heal_is_not_put_to_a_model() -> None:
    page = browser()
    renamed(page)
    model = FakeModel([reply(1)])
    unverifiable = export_step(checkpoints=[{"kind": "no_error_banner"}])

    result = await climb_with(page, chooser_for(model), target=unverifiable)

    assert (rung3(result).outcome, result.abstention) == (
        RungOutcome.NOT_ASKED,
        AbstentionReason.UNVERIFIABLE,
    )
    assert model.requests == []


async def test_a_step_out_of_heal_attempts_is_not_put_to_a_model() -> None:
    page = browser()
    renamed(page)
    model = FakeModel([reply(1)])

    result = await climb_with(page, chooser_for(model), used=2)

    assert result.abstention is AbstentionReason.ATTEMPTS_EXHAUSTED
    assert model.requests == []


async def test_a_sign_in_step_gets_its_one_attempt_before_a_model_is_asked() -> None:
    sign_in = export_button(accessible_name="Sign in", text="Sign in")
    config = healing()

    assert before_asking(export_step(sign_in), used=0, config=config) is None
    assert (
        before_asking(export_step(sign_in), used=1, config=config)
        is AbstentionReason.AUTHENTICATION_LIMIT
    )
    assert before_asking(export_step(), used=1, config=config) is None


async def test_a_verified_rung3_heal_is_found_again_without_asking_a_model() -> None:
    page = browser()
    renamed(page)
    summary(page)
    first = await climb_with(page, chooser_for(FakeModel([reply(1)])))
    assert first.accepted is not None
    signature = first.accepted.scored.signature
    silent = FakeModel([])

    again = await climb_with(page, chooser_for(silent), reuse=signature)
    gone = await climb_with(page, chooser_for(silent), reuse=("someone", "else"))

    assert again.accepted is not None
    assert again.accepted.scored.signature == signature
    assert gone.accepted is None
    assert silent.requests == []
