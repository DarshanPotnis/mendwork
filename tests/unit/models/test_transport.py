"""Provider HTTP: one time limit per call, backoff with jitter on 429 and 5xx, bounded replies."""

from collections.abc import AsyncIterator
from typing import Final

import httpx
import pytest
import pytest_asyncio
import respx

from mendwork.engine.errors import ProviderError
from tests.fakes.timer import FakeTimer
from tests.unit.models.helpers import transport

pytestmark = pytest.mark.asyncio

URL: Final = "https://models.example.test/v1/choose"
OK: Final = httpx.Response(200, json={"answer": "ok"})


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as http:
        yield http


async def post(client: httpx.AsyncClient, timer: FakeTimer, *, timeout_ms: int = 20_000) -> bytes:
    reply = await transport(client, timer).post_json(
        URL, {"question": "which"}, headers={"x-test": "1"}, timeout_ms=timeout_ms
    )
    return reply.body


async def test_a_reply_arrives_with_the_body_sent_as_json_and_the_time_left_as_its_timeout(
    client: httpx.AsyncClient,
) -> None:
    timer = FakeTimer()
    with respx.mock() as router:
        route = router.post(URL).mock(return_value=OK)
        body = await post(client, timer)

    assert body == b'{"answer":"ok"}'
    request = route.calls.last.request
    assert request.content == b'{"question":"which"}'
    assert request.headers["x-test"] == "1"
    assert request.extensions["timeout"] == {
        "connect": 20.0,
        "read": 20.0,
        "write": 20.0,
        "pool": 20.0,
    }
    assert timer.pauses == []


async def test_429_is_retried_after_its_retry_after(client: httpx.AsyncClient) -> None:
    timer = FakeTimer()
    with respx.mock() as router:
        route = router.post(URL).mock(
            side_effect=[httpx.Response(429, headers={"retry-after": "1"}), OK]
        )
        await post(client, timer)

    assert route.call_count == 2
    assert timer.pauses == [1.0]


async def test_5xx_and_timeouts_are_retried_with_growing_pauses(client: httpx.AsyncClient) -> None:
    timer = FakeTimer()
    with respx.mock() as router:
        route = router.post(URL).mock(
            side_effect=[httpx.Response(503), httpx.ReadTimeout("slow"), OK]
        )
        await post(client, timer)

    assert route.call_count == 3
    assert timer.pauses == [0.5, 1.0]
    second = route.calls[1].request.extensions["timeout"]
    assert second["read"] == pytest.approx(19.5)


async def test_a_provider_that_keeps_failing_is_given_up_on_after_the_last_attempt(
    client: httpx.AsyncClient,
) -> None:
    timer = FakeTimer()
    with respx.mock() as router:
        router.post(URL).mock(
            return_value=httpx.Response(500, json={"error": {"message": "internal   error"}})
        )
        with pytest.raises(ProviderError) as caught:
            await post(client, timer)

    assert caught.value.context == {"reason": "http_status", "status": 500, "attempts": 3}
    assert caught.value.message == "the provider answered HTTP 500: internal error"


async def test_a_connection_that_cannot_be_made_is_retried_then_reported(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock() as router:
        router.post(URL).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(ProviderError) as caught:
            await post(client, FakeTimer())

    assert caught.value.context["reason"] == "connection"
    assert caught.value.context["attempts"] == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_other_4xx_answers_will_not_change_and_are_not_retried(
    client: httpx.AsyncClient, status: int
) -> None:
    with respx.mock() as router:
        route = router.post(URL).mock(
            return_value=httpx.Response(status, text="model 'x' not found")
        )
        with pytest.raises(ProviderError) as caught:
            await post(client, FakeTimer())

    assert route.call_count == 1
    assert caught.value.message == f"the provider answered HTTP {status}: model 'x' not found"


async def test_a_retry_after_longer_than_the_longest_pause_ends_the_call(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock() as router:
        route = router.post(URL).mock(
            return_value=httpx.Response(429, headers={"retry-after": "60"})
        )
        with pytest.raises(ProviderError) as caught:
            await post(client, FakeTimer())

    assert route.call_count == 1
    assert caught.value.context["reason"] == "rate_limited"


async def test_a_pause_that_would_not_fit_in_the_time_left_ends_the_call(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock() as router:
        route = router.post(URL).mock(return_value=httpx.Response(503))
        with pytest.raises(ProviderError):
            await post(client, FakeTimer(), timeout_ms=400)

    assert route.call_count == 1


async def test_a_reply_larger_than_the_limit_is_refused_and_not_retried(
    client: httpx.AsyncClient,
) -> None:
    with respx.mock() as router:
        route = router.post(URL).mock(return_value=httpx.Response(200, content=b"x" * 5_000))
        with pytest.raises(ProviderError) as caught:
            await post(client, FakeTimer())

    assert route.call_count == 1
    assert caught.value.context["reason"] == "response_too_large"
