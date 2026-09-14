"""HTTP for model providers: one time limit per call, retries with backoff, and bounded replies.

- **Time.** A call's attempts, pauses included, fit inside the request's ``timeout_ms``; each
  attempt's HTTP timeout is the time left.
- **Retries.** Timeouts, dropped connections, 408, 429, and 5xx responses are tried again with
  the replayer's exponential backoff and jitter. A Retry-After given in seconds is honoured
  when it is no longer than the longest configured pause; a longer one ends the call, because
  waiting it out inside a heal is pointless. Other 4xx responses will not change and are not
  retried.
- **Size.** A reply larger than the configured byte limit is refused without being read whole.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import httpx
from pydantic import JsonValue

from mendwork.engine.errors import ProviderError
from mendwork.engine.ports.randomness import RandomSource
from mendwork.engine.ports.timer import Timer
from mendwork.engine.replay.config import RetryPolicy
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.retry import backoff_delay_ms

RETRYABLE_STATUSES: Final = frozenset({408, 429, 500, 502, 503, 504})
_DETAIL_MAX_CHARS: Final = 200


@dataclass(frozen=True, slots=True)
class TransportPolicy:
    """How hard a provider call tries, and how large a reply may be."""

    max_attempts: int
    retry: RetryPolicy
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class HttpReply:
    """A successful response body, and how many requests it took."""

    body: bytes
    attempts: int


@dataclass(frozen=True, slots=True)
class _Failure:
    reason: str
    message: str
    retryable: bool
    status: int | None = None
    retry_after_ms: int | None = None

    def error(self, attempts: int) -> ProviderError:
        return ProviderError(
            self.message, reason=self.reason, status=self.status, attempts=attempts
        )


class _ResponseTooLargeError(Exception):
    """A reply body passed the size limit while it was being read, so reading stopped."""


class ModelHttpClient:
    """Posts JSON to a provider within a call's time limit."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        policy: TransportPolicy,
        timer: Timer,
        randomness: RandomSource,
    ) -> None:
        self._client = client
        self._policy = policy
        self._timer = timer
        self._randomness = randomness

    async def post_json(
        self,
        url: str,
        payload: Mapping[str, JsonValue],
        *,
        headers: Mapping[str, str],
        timeout_ms: int,
    ) -> HttpReply:
        """The provider's 2xx reply, or ProviderError once no attempt or time is left."""
        deadline = Deadline.after(self._timer, timeout_ms)
        attempt = 0
        while True:
            attempt += 1
            outcome = await self._attempt(url, payload, headers, deadline, attempt)
            if isinstance(outcome, HttpReply):
                return outcome
            if not outcome.retryable or attempt >= self._policy.max_attempts:
                raise outcome.error(attempt)
            pause_ms = self._pause_ms(attempt, outcome.retry_after_ms)
            if pause_ms is None or pause_ms >= deadline.remaining_ms():
                raise outcome.error(attempt)
            await self._timer.pause(pause_ms / 1000)

    async def _attempt(
        self,
        url: str,
        payload: Mapping[str, JsonValue],
        headers: Mapping[str, str],
        deadline: Deadline,
        attempt: int,
    ) -> HttpReply | _Failure:
        remaining_ms = deadline.remaining_ms()
        if remaining_ms == 0:
            return _Failure("timeout", "the provider did not answer in time", retryable=False)
        try:
            async with self._client.stream(
                "POST",
                url,
                json=dict(payload),
                headers=dict(headers),
                timeout=httpx.Timeout(remaining_ms / 1000),
            ) as response:
                body = await self._read(response)
        except httpx.TimeoutException:
            return _Failure("timeout", "the provider did not answer in time", retryable=True)
        except httpx.TransportError as error:
            return _Failure(
                "connection",
                f"the provider could not be reached ({type(error).__name__})",
                retryable=True,
            )
        except _ResponseTooLargeError:
            return _Failure(
                "response_too_large",
                f"the provider's reply was larger than {self._policy.max_response_bytes} bytes",
                retryable=False,
            )
        status = response.status_code
        if 200 <= status < 300:
            return HttpReply(body, attempt)
        retry_after = _retry_after_ms(response.headers.get("retry-after"))
        detail = _detail(body)
        if status == 429:
            return _Failure(
                "rate_limited",
                f"the provider refused the call as too many requests{detail}",
                retryable=True,
                status=status,
                retry_after_ms=retry_after,
            )
        return _Failure(
            "http_status",
            f"the provider answered HTTP {status}{detail}",
            retryable=status in RETRYABLE_STATUSES,
            status=status,
            retry_after_ms=retry_after,
        )

    async def _read(self, response: httpx.Response) -> bytes:
        limit = self._policy.max_response_bytes
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                raise _ResponseTooLargeError
            chunks.append(chunk)
        return b"".join(chunks)

    def _pause_ms(self, failed_attempt: int, retry_after_ms: int | None) -> float | None:
        retry = self._policy.retry
        pause = backoff_delay_ms(failed_attempt, retry, self._randomness.unit())
        if retry_after_ms is None:
            return pause
        if retry_after_ms > retry.max_delay_ms:
            return None
        return max(pause, float(retry_after_ms))


def _retry_after_ms(value: str | None) -> int | None:
    if value is None or not value.strip().isdigit():
        return None
    return int(value.strip()) * 1000


def _detail(body: bytes) -> str:
    """A short explanation from an error body, when the provider gave one."""
    text = body.decode("utf-8", errors="replace").strip()
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        message = text
    else:
        error = document.get("error") if isinstance(document, dict) else None
        if isinstance(error, dict):
            error = error.get("message")
        message = error if isinstance(error, str) else ""
    message = " ".join(message.split())[:_DETAIL_MAX_CHARS]
    return f": {message}" if message else ""
