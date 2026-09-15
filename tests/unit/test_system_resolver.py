"""The system resolver: loopback names resolve, failures and timeouts become navigation errors."""

import asyncio
import socket

import pytest

from mendwork.adapters.system.resolver import NAME_NOT_RESOLVED, SystemHostResolver
from mendwork.engine.errors import NavigationError

pytestmark = pytest.mark.asyncio

AddressInfo = tuple[int, int, int, str, tuple[str, int] | tuple[str, int, int, int]]


async def test_localhost_resolves_to_loopback_addresses_without_duplicates() -> None:
    addresses = await SystemHostResolver().resolve("localhost", timeout_ms=5_000)

    assert addresses
    assert set(addresses) <= {"127.0.0.1", "::1"}
    assert len(addresses) == len(set(addresses))


async def test_zones_are_stripped_and_repeated_addresses_kept_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer: list[AddressInfo] = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%en0", 0, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: answer)

    addresses = await SystemHostResolver().resolve("intranet.example.test", timeout_ms=1_000)

    assert addresses == ("fe80::1", "10.0.0.5")


async def test_a_name_that_does_not_resolve_is_a_navigation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> list[AddressInfo]:
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", fail)

    with pytest.raises(NavigationError) as caught:
        await SystemHostResolver().resolve("missing.example.test", timeout_ms=1_000)

    assert caught.value.context["reason"] == NAME_NOT_RESOLVED
    assert caught.value.context["host"] == "missing.example.test"


async def test_an_empty_answer_is_a_navigation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [])

    with pytest.raises(NavigationError, match="no address"):
        await SystemHostResolver().resolve("empty.example.test", timeout_ms=1_000)


async def test_a_lookup_that_outlives_its_timeout_is_a_navigation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The loop's own lookup never answers; no thread is left blocked behind the test.
    async def never(*_args: object, **_kwargs: object) -> list[AddressInfo]:
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", never)

    with pytest.raises(NavigationError) as caught:
        await SystemHostResolver().resolve("slow.example.test", timeout_ms=20)

    assert caught.value.context["error_type"] == "TimeoutError"
