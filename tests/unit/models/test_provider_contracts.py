"""Each provider's wire contract, replayed from recorded fixtures: what is sent, and how the reply,
its token counts, and its failure shapes are read. No test reaches a provider.
"""

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Final

import httpx
import pytest
import pytest_asyncio
import respx
from pydantic import JsonValue, SecretStr

from mendwork.adapters.models.gemini import GeminiOptions, GeminiWire
from mendwork.adapters.models.ollama import OllamaOptions, OllamaWire
from mendwork.adapters.models.openai_compatible import (
    OpenAICompatibleOptions,
    OpenAICompatibleWire,
)
from mendwork.adapters.models.pricing import ModelPrice
from mendwork.engine.errors import ModelOutputInvalid, ProviderError
from mendwork.engine.healing.choice import ParsedChoice, parse_choice
from mendwork.engine.healing.prompt import SYSTEM_PROMPT, repair_messages
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.timer import FakeTimer
from tests.unit.models.helpers import REQUEST, choice_json, model

pytestmark = pytest.mark.asyncio

FIXTURES: Final = Path(__file__).resolve().parents[2] / "fixtures" / "models"
OLLAMA_URL: Final = "http://127.0.0.1:11434/api/chat"
GEMINI_BASE: Final = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_URL: Final = f"{GEMINI_BASE}/models/flash-example:generateContent"
COMPATIBLE_URL: Final = "https://llm.example.test/v1/chat/completions"
KEY: Final = SecretStr("test-key-not-real")
PRICE: Final = ModelPrice(
    input_usd_per_million_tokens=Decimal("0.10"), output_usd_per_million_tokens=Decimal("0.40")
)


def fixture(name: str) -> dict[str, JsonValue]:
    loaded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as http:
        yield http


def ollama(think: bool | None = None) -> OllamaWire:
    return OllamaWire(
        OllamaOptions(
            base_url="http://127.0.0.1:11434/",
            model="local-chooser:4b",
            temperature=0.0,
            seed=0,
            max_output_tokens=200,
            context_tokens=4_096,
            keep_alive="5m",
            think=think,
        )
    )


def gemini(think: bool | None = None) -> GeminiWire:
    return GeminiWire(
        GeminiOptions(
            base_url=GEMINI_BASE,
            model="flash-example",
            api_key=KEY,
            temperature=0.0,
            seed=0,
            max_output_tokens=200,
            think=think,
        )
    )


def compatible(key: SecretStr | None = KEY, *, local: bool = False) -> OpenAICompatibleWire:
    return OpenAICompatibleWire(
        OpenAICompatibleOptions(
            base_url="https://llm.example.test/v1",
            model="chooser-example",
            api_key=key,
            temperature=0.0,
            seed=0,
            max_output_tokens=200,
            local=local,
        )
    )


def messages_as(role_names: dict[str, str]) -> list[JsonValue]:
    return [
        {"role": role_names.get(m.role.value, m.role.value), "content": m.text}
        for m in REQUEST.messages
    ]


async def test_ollama_is_asked_for_schema_constrained_repeatable_bounded_output() -> None:
    assert ollama().url() == OLLAMA_URL
    assert ollama().headers() == {}
    assert ollama().encode(REQUEST) == {
        "model": "local-chooser:4b",
        "messages": messages_as({}),
        "stream": False,
        "format": REQUEST.response_schema,
        "keep_alive": "5m",
        "options": {"temperature": 0.0, "seed": 0, "num_predict": 200, "num_ctx": 4_096},
    }
    assert ollama(think=False).encode(REQUEST)["think"] is False


async def test_ollamas_recorded_reply_is_read_with_its_token_counts(
    client: httpx.AsyncClient,
) -> None:
    recorded = fixture("ollama_chat_ok.json")
    message = recorded["message"]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, str)
    expected = parse_choice(content)
    assert isinstance(expected, ParsedChoice)
    with respx.mock() as router:
        route = router.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=recorded))
        result = await model(
            ollama(), client, FakeTimer(), name="local-chooser:4b"
        ).choose_candidate(REQUEST)

    assert json.loads(route.calls.last.request.content) == ollama().encode(REQUEST)
    assert (result.choice, result.confidence, result.reason) == (
        expected.choice,
        expected.confidence,
        expected.reason,
    )
    usage = result.usage
    assert (usage.provider, usage.model, usage.http_attempts) == ("ollama", "local-chooser:4b", 1)
    assert (usage.input_tokens, usage.output_tokens) == (
        recorded["prompt_eval_count"],
        recorded["eval_count"],
    )
    assert usage.estimated_cost_usd == 0


async def test_an_ollama_reply_cut_off_at_the_token_limit_is_invalid_output(
    client: httpx.AsyncClient,
) -> None:
    cut = {
        "message": {"role": "assistant", "content": '{"choice": 1, "conf'},
        "done_reason": "length",
    }
    with respx.mock() as router:
        router.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=cut))
        with pytest.raises(ModelOutputInvalid) as caught:
            await model(ollama(), client, FakeTimer(), name="m").choose_candidate(REQUEST)

    assert caught.value.context["problem"] == "it was cut off before it ended"
    assert caught.value.context["usage"] is not None


async def test_a_missing_model_and_a_malformed_reply_are_provider_errors_with_usage(
    client: httpx.AsyncClient,
) -> None:
    missing = httpx.Response(404, json={"error": "model 'local-chooser:4b' not found"})
    with respx.mock() as router:
        router.post(OLLAMA_URL).mock(
            side_effect=[missing, httpx.Response(200, json={"done": True})]
        )
        adapter = model(ollama(), client, FakeTimer(), name="local-chooser:4b")
        with pytest.raises(ProviderError) as not_found:
            await adapter.choose_candidate(REQUEST)
        with pytest.raises(ProviderError) as malformed:
            await adapter.choose_candidate(REQUEST)

    assert not_found.value.message == (
        "the provider answered HTTP 404: model 'local-chooser:4b' not found"
    )
    assert malformed.value.context["reason"] == "malformed_response"
    assert malformed.value.context["usage"] is not None


async def test_three_failed_calls_open_the_breaker_and_the_next_sends_nothing(
    client: httpx.AsyncClient,
) -> None:
    timer = FakeTimer()
    with respx.mock() as router:
        route = router.post(OLLAMA_URL).mock(return_value=httpx.Response(400))
        adapter = model(ollama(), client, timer, name="m")
        for _ in range(3):
            with pytest.raises(ProviderError):
                await adapter.choose_candidate(REQUEST)
        with pytest.raises(ProviderError) as paused:
            await adapter.choose_candidate(REQUEST)

    assert route.call_count == 3
    assert paused.value.context["reason"] == "circuit_open"


async def test_gemini_is_asked_with_its_own_roles_schema_and_key_header() -> None:
    wire = gemini()
    repair = REQUEST.model_copy(
        update={
            "messages": repair_messages(
                REQUEST.messages, reply="nope", problem="it was empty", scrubber=SecretScrubber()
            )
        }
    )

    assert wire.url() == GEMINI_URL
    assert wire.headers() == {"x-goog-api-key": "test-key-not-real"}
    body = wire.encode(repair)
    assert body["systemInstruction"] == {"parts": [{"text": SYSTEM_PROMPT}]}
    contents = body["contents"]
    assert isinstance(contents, list)
    assert [item["role"] for item in contents if isinstance(item, dict)] == [
        "user",
        "model",
        "user",
    ]
    assert body["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseJsonSchema": REQUEST.response_schema,
        "temperature": 0.0,
        "seed": 0,
        "maxOutputTokens": 200,
    }
    config = gemini(think=False).encode(REQUEST)["generationConfig"]
    assert isinstance(config, dict)
    assert config["thinkingConfig"] == {"thinkingBudget": 0}


async def test_geminis_documented_reply_is_read_and_priced(client: httpx.AsyncClient) -> None:
    with respx.mock() as router:
        router.post(GEMINI_URL).mock(
            return_value=httpx.Response(200, json=fixture("gemini_generate_ok.json"))
        )
        result = await model(
            gemini(), client, FakeTimer(), name="flash-example", price=PRICE
        ).choose_candidate(REQUEST)

    assert (result.choice, result.reason) == (1, "Same control, renamed.")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (431, 24)
    assert result.usage.estimated_cost_usd == Decimal("0.0000527")


@pytest.mark.parametrize(
    ("reply", "problem"),
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "the provider withheld the answer"),
        (
            {"candidates": [{"content": {"parts": [{"text": "{"}]}, "finishReason": "MAX_TOKENS"}]},
            "it was cut off before it ended",
        ),
        (
            {
                "candidates": [
                    {"content": {"parts": [{"text": choice_json()}]}, "finishReason": "SAFETY"}
                ]
            },
            "the provider withheld the answer",
        ),
    ],
)
async def test_gemini_blocks_and_cut_offs_are_invalid_output(
    client: httpx.AsyncClient, reply: dict[str, JsonValue], problem: str
) -> None:
    with respx.mock() as router:
        router.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=reply))
        with pytest.raises(ModelOutputInvalid) as caught:
            await model(gemini(), client, FakeTimer(), name="flash-example").choose_candidate(
                REQUEST
            )

    assert caught.value.context["problem"] == problem


async def test_gemini_thought_parts_are_not_the_answer_but_their_tokens_count(
    client: httpx.AsyncClient,
) -> None:
    reply = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Let me think about it", "thought": True},
                        {"text": choice_json(2)},
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 400,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": 80,
        },
    }
    with respx.mock() as router:
        router.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=reply))
        result = await model(gemini(), client, FakeTimer(), name="flash-example").choose_candidate(
            REQUEST
        )

    assert (result.choice, result.usage.output_tokens) == (2, 100)
    assert result.usage.estimated_cost_usd is None


async def test_gemini_without_a_candidate_is_a_malformed_reply(client: httpx.AsyncClient) -> None:
    with respx.mock() as router:
        router.post(GEMINI_URL).mock(return_value=httpx.Response(200, json={"candidates": []}))
        with pytest.raises(ProviderError) as caught:
            await model(gemini(), client, FakeTimer(), name="flash-example").choose_candidate(
                REQUEST
            )

    assert caught.value.context["reason"] == "malformed_response"


async def test_an_openai_compatible_server_is_asked_for_a_strict_json_schema() -> None:
    wire = compatible()

    assert wire.url() == COMPATIBLE_URL
    assert wire.headers() == {"Authorization": "Bearer test-key-not-real"}
    assert compatible(key=None).headers() == {}
    assert wire.encode(REQUEST) == {
        "model": "chooser-example",
        "messages": messages_as({}),
        "temperature": 0.0,
        "seed": 0,
        "max_tokens": 200,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "candidate_choice",
                "schema": REQUEST.response_schema,
                "strict": True,
            },
        },
    }


async def test_an_openai_compatible_documented_reply_is_read_and_a_local_server_is_free(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock() as router:
        router.post(COMPATIBLE_URL).mock(
            return_value=httpx.Response(200, json=fixture("openai_chat_ok.json"))
        )
        hosted = await model(
            compatible(), client, FakeTimer(), name="chooser-example"
        ).choose_candidate(REQUEST)
        local = await model(
            compatible(local=True), client, FakeTimer(), name="chooser-example"
        ).choose_candidate(REQUEST)

    assert (hosted.choice, hosted.usage.input_tokens, hosted.usage.output_tokens) == (1, 431, 24)
    assert hosted.usage.estimated_cost_usd is None
    assert local.usage.estimated_cost_usd == 0


@pytest.mark.parametrize(
    ("choice", "problem"),
    [
        (
            {
                "message": {"role": "assistant", "content": None, "refusal": "I can't help."},
                "finish_reason": "stop",
            },
            "the provider withheld the answer",
        ),
        (
            {
                "message": {"role": "assistant", "content": '{"choice": 1'},
                "finish_reason": "length",
            },
            "it was cut off before it ended",
        ),
        (
            {"message": {"role": "assistant", "content": ""}, "finish_reason": "content_filter"},
            "the provider withheld the answer",
        ),
    ],
)
async def test_openai_compatible_refusals_and_cut_offs_are_invalid_output(
    client: httpx.AsyncClient, choice: dict[str, JsonValue], problem: str
) -> None:
    with respx.mock() as router:
        router.post(COMPATIBLE_URL).mock(
            return_value=httpx.Response(200, json={"choices": [choice]})
        )
        with pytest.raises(ModelOutputInvalid) as caught:
            await model(compatible(), client, FakeTimer(), name="c").choose_candidate(REQUEST)

    assert caught.value.context["problem"] == problem


async def test_an_openai_compatible_reply_without_choices_is_malformed(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock() as router:
        router.post(COMPATIBLE_URL).mock(return_value=httpx.Response(200, content=b"[]"))
        with pytest.raises(ProviderError) as caught:
            await model(compatible(), client, FakeTimer(), name="c").choose_candidate(REQUEST)

    assert caught.value.context["reason"] == "malformed_response"
