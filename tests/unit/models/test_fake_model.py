"""The scripted model replies in order or from a function, through the same parsing as providers."""

import pytest

from mendwork.adapters.models.fake import FakeModel
from mendwork.engine.errors import ModelOutputInvalid, ProviderError
from mendwork.engine.ports.model_types import ChoiceRequest
from tests.unit.models.helpers import REQUEST, choice_json

pytestmark = pytest.mark.asyncio


async def test_scripted_replies_are_parsed_in_order_and_every_request_is_kept() -> None:
    model = FakeModel([choice_json(2), choice_json(None)], input_tokens=500, output_tokens=20)

    first = await model.choose_candidate(REQUEST)
    second = await model.choose_candidate(REQUEST)

    assert (first.choice, second.choice) == (2, None)
    assert first.usage.input_tokens == 500
    assert first.usage.estimated_cost_usd == 0
    assert model.requests == [REQUEST, REQUEST]


async def test_a_responder_answers_from_the_request() -> None:
    def respond(request: ChoiceRequest) -> str:
        return choice_json(len(request.shown))

    assert (await FakeModel(respond).choose_candidate(REQUEST)).choice == 3


async def test_a_scripted_provider_error_is_raised_with_the_calls_usage() -> None:
    model = FakeModel([ProviderError("down", reason="connection")])

    with pytest.raises(ProviderError) as caught:
        await model.choose_candidate(REQUEST)

    assert caught.value.context["reason"] == "connection"
    assert caught.value.context["usage"] is not None


async def test_an_unusable_reply_is_invalid_output_and_an_empty_script_is_an_error() -> None:
    model = FakeModel(["Sure, number one!"])

    with pytest.raises(ModelOutputInvalid) as invalid:
        await model.choose_candidate(REQUEST)
    with pytest.raises(ProviderError) as exhausted:
        await model.choose_candidate(REQUEST)

    assert invalid.value.context["problem"] == "it was not exactly one JSON object"
    assert invalid.value.context["excerpt"] == "Sure, number one!"
    assert exhausted.value.context["reason"] == "script_exhausted"
