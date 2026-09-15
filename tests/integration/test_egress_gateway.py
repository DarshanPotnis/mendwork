"""The SOCKS5 egress gateway over loopback sockets: every connection checked, names resolved once.

No browser: a test speaks SOCKS5 to the gateway the way Chromium does, and the only servers are
this machine's own.
"""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import pytest

from mendwork.adapters.browser_playwright.egress.gateway import EgressGateway
from mendwork.adapters.browser_playwright.egress.log import EgressLog
from mendwork.engine.errors import MendworkError, NavigationError
from mendwork.engine.safety.egress import EgressPolicy, LoopbackException
from mendwork.engine.safety.egress_addresses import AddressRange
from mendwork.engine.safety.egress_blocks import EgressLayer
from tests.fakes.egress import FakeResolver

pytestmark = pytest.mark.asyncio

NAME: Final = 3
IPV4: Final = 1
IPV6: Final = 4
SUCCEEDED: Final = 0
NOT_ALLOWED: Final = 2
HOST_UNREACHABLE: Final = 4
CONNECTION_REFUSED: Final = 5
COMMAND_NOT_SUPPORTED: Final = 7
ADDRESS_NOT_SUPPORTED: Final = 8


@asynccontextmanager
async def echo_server() -> AsyncIterator[int]:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(await reader.read(64))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@asynccontextmanager
async def gateway(
    policy: EgressPolicy, resolver: FakeResolver | None = None, *, timeout_ms: int = 2_000
) -> AsyncIterator[tuple[int, EgressLog]]:
    log = EgressLog()
    async with EgressGateway(
        policy=policy, resolver=resolver or FakeResolver(), log=log, timeout_ms=timeout_ms
    ) as opened:
        yield int(opened.proxy_server.rsplit(":", 1)[1]), log


def excepting(*ports: int) -> EgressPolicy:
    return EgressPolicy(
        loopback_exceptions=tuple(LoopbackException(address="127.0.0.1", port=p) for p in ports)
    )


def address(kind: int, host: str) -> bytes:
    if kind == IPV4:
        return socket.inet_aton(host)
    if kind == IPV6:
        return socket.inet_pton(socket.AF_INET6, host)
    raw = host.encode("latin-1")
    return bytes([len(raw)]) + raw


async def connect(
    gateway_port: int,
    host: str,
    port: int,
    *,
    kind: int = NAME,
    command: int = 1,
    raw_host: bytes | None = None,
) -> tuple[int, asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway_port)
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    assert await reader.readexactly(2) == b"\x05\x00"
    target = raw_host if raw_host is not None else address(kind, host)
    writer.write(bytes([5, command, 0, kind]) + target + port.to_bytes(2, "big"))
    await writer.drain()
    reply = await reader.readexactly(10)
    return reply[1], reader, writer


async def test_an_excepted_loopback_origin_is_relayed_without_a_lookup() -> None:
    resolver = FakeResolver()
    async with echo_server() as echo, gateway(excepting(echo), resolver) as (port, log):
        code, reader, writer = await connect(port, "127.0.0.1", echo)
        writer.write(b"ping")
        await writer.drain()
        echoed = await reader.read(64)
        writer.close()

    assert (code, echoed) == (SUCCEEDED, b"ping")
    assert (resolver.lookups, log.take_blocks()) == ([], ())


async def test_an_ipv4_address_is_checked_the_same_as_a_named_literal() -> None:
    async with echo_server() as echo, gateway(excepting(echo)) as (port, _log):
        code, _reader, writer = await connect(port, "127.0.0.1", echo, kind=IPV4)
        writer.close()

    assert code == SUCCEEDED


async def test_loopback_on_a_port_without_an_exception_is_refused_and_recorded() -> None:
    async with echo_server() as echo, gateway(EgressPolicy()) as (port, log):
        code, _reader, writer = await connect(port, "127.0.0.1", echo)
        writer.close()

    [block] = log.take_blocks()
    assert code == NOT_ALLOWED
    assert (block.layer, block.refusal.address_range) == (
        EgressLayer.CONNECTION,
        AddressRange.LOOPBACK,
    )


async def test_a_name_that_resolves_to_a_private_address_is_refused_after_one_lookup() -> None:
    resolver = FakeResolver({"rebind.example.test": ("93.184.215.14", "10.0.0.5")})
    async with gateway(EgressPolicy(), resolver) as (port, log):
        code, _reader, writer = await connect(port, "rebind.example.test", 443)
        writer.close()

    [block] = log.take_blocks()
    assert code == NOT_ALLOWED
    assert (block.refusal.address, block.refusal.address_range) == (
        "10.0.0.5",
        AddressRange.PRIVATE,
    )
    assert resolver.lookups == ["rebind.example.test"]


async def test_a_name_resolving_to_an_excepted_address_is_still_refused() -> None:
    resolver = FakeResolver({"loopback.example.test": ("127.0.0.1",)})
    async with echo_server() as echo, gateway(excepting(echo), resolver) as (port, log):
        code, _reader, writer = await connect(port, "loopback.example.test", echo)
        writer.close()

    assert code == NOT_ALLOWED
    assert len(log.take_blocks()) == 1


async def test_a_metadata_address_is_refused_over_ipv6() -> None:
    async with gateway(EgressPolicy()) as (port, log):
        code, _reader, writer = await connect(port, "fd00:ec2::254", 80, kind=IPV6)
        writer.close()

    [block] = log.take_blocks()
    assert (code, block.refusal.address_range) == (NOT_ALLOWED, AddressRange.CLOUD_METADATA)


async def test_a_name_that_does_not_resolve_is_an_upstream_failure_not_a_refusal() -> None:
    failed = NavigationError("no such host", reason="ERR_NAME_RESOLUTION_FAILED")
    async with gateway(EgressPolicy(), FakeResolver(failure=failed)) as (port, log):
        mark = log.mark()
        code, _reader, writer = await connect(port, "missing.example.test", 443)
        writer.close()

    failure = log.failure_since(mark)
    assert code == HOST_UNREACHABLE
    assert failure is not None
    assert (failure.host, failure.reason) == ("missing.example.test", "ERR_NAME_RESOLUTION_FAILED")
    assert log.take_blocks() == ()


async def test_a_refused_upstream_is_recorded_with_chromiums_own_reason() -> None:
    dead = closed_port()
    async with gateway(excepting(dead)) as (port, log):
        code, _reader, writer = await connect(port, "127.0.0.1", dead)
        writer.close()

    failure = log.failure_since(0)
    assert code == CONNECTION_REFUSED
    assert failure is not None
    assert failure.reason == "ERR_CONNECTION_REFUSED"


async def test_only_connect_and_known_address_types_are_spoken() -> None:
    async with gateway(EgressPolicy()) as (port, log):
        bind, _r1, w1 = await connect(port, "127.0.0.1", 80, command=2)
        unknown, _r2, w2 = await connect(port, "", 80, kind=9, raw_host=b"")
        not_ascii, _r3, w3 = await connect(port, "", 80, raw_host=b"\x02\xff\xfe")
        for writer in (w1, w2, w3):
            writer.close()

    assert (bind, unknown, not_ascii) == (
        COMMAND_NOT_SUPPORTED,
        ADDRESS_NOT_SUPPORTED,
        ADDRESS_NOT_SUPPORTED,
    )
    assert log.take_blocks() == ()


async def test_a_client_offering_no_acceptable_method_is_turned_away() -> None:
    async with gateway(EgressPolicy()) as (port, _log):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"\x05\x01\x02")
        await writer.drain()
        answer = await reader.readexactly(2)
        writer.close()

    assert answer == b"\x05\xff"


async def test_a_stalled_handshake_is_closed_at_the_timeout_without_a_record() -> None:
    async with gateway(EgressPolicy(), timeout_ms=50) as (port, log):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        closed = await reader.read(1)
        writer.close()

    assert (closed, log.take_blocks(), log.failure_since(0)) == (b"", (), None)


async def test_closing_the_gateway_ends_relays_still_open() -> None:
    async with echo_server() as echo:
        async with gateway(excepting(echo)) as (port, _log):
            code, reader, writer = await connect(port, "127.0.0.1", echo)
        leftover = await reader.read(64)
        writer.close()

    assert (code, leftover) == (SUCCEEDED, b"")


async def test_the_gateway_has_no_address_before_it_starts() -> None:
    idle = EgressGateway(
        policy=EgressPolicy(), resolver=FakeResolver(), log=EgressLog(), timeout_ms=1_000
    )

    with pytest.raises(MendworkError, match="has not started"):
        _ = idle.proxy_server
