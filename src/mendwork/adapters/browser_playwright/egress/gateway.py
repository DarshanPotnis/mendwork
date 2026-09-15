"""A SOCKS5 gateway that holds every browser connection to the run's egress policy (ADR 0011).

Chromium sends every connection of the run's context through this gateway: each redirect hop,
subresources, WebSockets, and pages the run did not open. Playwright's request routing was
measured to see only the first request of a redirect chain, so it cannot do this job.

For each connection the gateway reads the host the browser asked for, resolves a name once,
refuses the connection when any address is internal or a metadata endpoint (unless the host is
literally one of the policy's loopback exceptions), and connects only to the addresses it checked.
The browser never resolves the name itself, so a name cannot resolve to a public address for the
check and a private one for the connection.

Only the part of SOCKS5 (RFC 1928) Chromium uses is spoken: no authentication, CONNECT, and IPv4,
name, and IPv6 addresses. Refusals and upstream failures go to the session's EgressLog, so a failed
navigation is reported as what it was.
"""

import asyncio
import socket
from types import TracebackType
from typing import Final, Self

import structlog

from mendwork.adapters.browser_playwright.egress.log import EgressLog, UpstreamFailure
from mendwork.engine.errors import MendworkError, NavigationError
from mendwork.engine.ports.resolver import HostResolver
from mendwork.engine.safety.egress import EgressPolicy, check_connection
from mendwork.engine.safety.egress_addresses import IPAddress, parse_address, parse_host
from mendwork.engine.safety.egress_blocks import EgressBlock, EgressLayer

_VERSION: Final = 5
_NO_AUTHENTICATION: Final = 0
_NO_ACCEPTABLE_METHOD: Final = 0xFF
_CONNECT: Final = 1
_IPV4: Final = 1
_NAME: Final = 3
_IPV6: Final = 4
_IPV4_LENGTH: Final = 4
_IPV6_LENGTH: Final = 16
_SUCCEEDED: Final = 0
_NOT_ALLOWED: Final = 2
_HOST_UNREACHABLE: Final = 4
_CONNECTION_REFUSED: Final = 5
_TTL_EXPIRED: Final = 6
_COMMAND_NOT_SUPPORTED: Final = 7
_ADDRESS_NOT_SUPPORTED: Final = 8
_CHUNK: Final = 64 * 1024
_LOOPBACK: Final = "127.0.0.1"
_NAME_NOT_RESOLVED: Final = "ERR_NAME_RESOLUTION_FAILED"
_REFUSED_UPSTREAM: Final = "ERR_CONNECTION_REFUSED"
_TIMED_OUT_UPSTREAM: Final = "ERR_CONNECTION_TIMED_OUT"
_UNREACHABLE_UPSTREAM: Final = "ERR_ADDRESS_UNREACHABLE"

Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]


class EgressGateway:
    """One session's SOCKS5 gateway on loopback, open while the context is used."""

    def __init__(
        self, *, policy: EgressPolicy, resolver: HostResolver, log: EgressLog, timeout_ms: int
    ) -> None:
        self._policy = policy
        self._resolver = resolver
        self._log = log
        self._timeout_ms = timeout_ms
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[None]] = set()
        self._logger = structlog.stdlib.get_logger("mendwork.egress")

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._accept, _LOOPBACK, 0)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        server = self._server
        if server is None:
            return
        server.close()
        for task in tuple(self._connections):
            task.cancel()
        await asyncio.gather(*self._connections, return_exceptions=True)
        await server.wait_closed()

    @property
    def proxy_server(self) -> str:
        """The gateway as a proxy URL for the browser context."""
        if self._server is None:
            raise MendworkError("the egress gateway has not started")
        port = self._server.sockets[0].getsockname()[1]
        return f"socks5://{_LOOPBACK}:{port}"

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
        try:
            await self._serve(reader, writer)
        finally:
            if task is not None:
                self._connections.discard(task)
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(self._timeout_ms / 1000):
                request = await self._handshake(reader, writer)
            upstream = None if request is None else await self._open(*request, writer)
        except (asyncio.IncompleteReadError, OSError) as error:
            # The browser went away or stalled mid-handshake; nothing was connected.
            self._logger.debug("egress_connection_abandoned", error_type=type(error).__name__)
            return
        if upstream is not None:
            await self._relay(reader, writer, upstream)

    async def _handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> tuple[str, int] | None:
        version, count = await reader.readexactly(2)
        methods = await reader.readexactly(count)
        if version != _VERSION or _NO_AUTHENTICATION not in methods:
            writer.write(bytes((_VERSION, _NO_ACCEPTABLE_METHOD)))
            await writer.drain()
            return None
        writer.write(bytes((_VERSION, _NO_AUTHENTICATION)))
        await writer.drain()
        version, command, _reserved, kind = await reader.readexactly(4)
        host = await _read_host(reader, kind)
        port = int.from_bytes(await reader.readexactly(2), "big")
        if version != _VERSION or command != _CONNECT:
            await _reply(writer, _COMMAND_NOT_SUPPORTED)
            return None
        if host is None:
            await _reply(writer, _ADDRESS_NOT_SUPPORTED)
            return None
        return host, port

    async def _open(self, host: str, port: int, writer: asyncio.StreamWriter) -> Streams | None:
        addresses = await self._addresses(host, port, writer)
        if addresses is None:
            return None
        refusal = check_connection(host, port, addresses, self._policy)
        if refusal is not None:
            self._log.block(EgressBlock(layer=EgressLayer.CONNECTION, refusal=refusal))
            self._logger.warning(
                "egress_connection_refused",
                host=refusal.host,
                port=port,
                rule=refusal.rule.value,
                address_range=None
                if refusal.address_range is None
                else refusal.address_range.value,
            )
            await _reply(writer, _NOT_ALLOWED)
            return None
        return await self._connect(host, port, addresses, writer)

    async def _addresses(
        self, host: str, port: int, writer: asyncio.StreamWriter
    ) -> tuple[IPAddress, ...] | None:
        try:
            parsed = parse_host(host)
        except ValueError:
            return ()  # check_connection refuses an unreadable host as malformed
        if parsed.address is not None:
            return (parsed.address,)
        try:
            found = await self._resolver.resolve(parsed.text, timeout_ms=self._timeout_ms)
            return tuple(parse_address(text) for text in found)
        except (NavigationError, ValueError):
            self._log.upstream_failure(UpstreamFailure(parsed.text, port, _NAME_NOT_RESOLVED))
            await _reply(writer, _HOST_UNREACHABLE)
            return None

    async def _connect(
        self,
        host: str,
        port: int,
        addresses: tuple[IPAddress, ...],
        writer: asyncio.StreamWriter,
    ) -> Streams | None:
        failure: OSError | None = None
        for address in addresses:
            try:
                async with asyncio.timeout(self._timeout_ms / 1000):
                    upstream = await asyncio.open_connection(str(address), port)
            except OSError as error:
                failure = error
                continue
            try:
                await _reply(writer, _SUCCEEDED)
            except OSError:
                upstream[1].close()
                raise
            return upstream
        reason, code = _upstream_failure(failure)
        self._log.upstream_failure(UpstreamFailure(host, port, reason))
        await _reply(writer, code)
        return None

    async def _relay(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, upstream: Streams
    ) -> None:
        upstream_reader, upstream_writer = upstream
        try:
            results = await asyncio.gather(
                _pipe(reader, upstream_writer),
                _pipe(upstream_reader, writer),
                return_exceptions=True,
            )
        finally:
            upstream_writer.close()
        for result in results:
            # A reset or closed socket ends a relay normally; anything else is worth knowing.
            if isinstance(result, BaseException) and not isinstance(result, OSError):
                self._logger.warning("egress_relay_failed", error_type=type(result).__name__)


async def _read_host(reader: asyncio.StreamReader, kind: int) -> str | None:
    if kind == _IPV4:
        return socket.inet_ntop(socket.AF_INET, await reader.readexactly(_IPV4_LENGTH))
    if kind == _IPV6:
        return f"[{socket.inet_ntop(socket.AF_INET6, await reader.readexactly(_IPV6_LENGTH))}]"
    if kind == _NAME:
        length = (await reader.readexactly(1))[0]
        raw = await reader.readexactly(length)
        try:
            name = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
        return f"[{name}]" if ":" in name and not name.startswith("[") else name
    return None


async def _reply(writer: asyncio.StreamWriter, code: int) -> None:
    writer.write(bytes((_VERSION, code, 0, _IPV4, 0, 0, 0, 0, 0, 0)))
    await writer.drain()


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    while data := await source.read(_CHUNK):
        sink.write(data)
        await sink.drain()
    if sink.can_write_eof() and not sink.is_closing():
        sink.write_eof()


def _upstream_failure(error: OSError | None) -> tuple[str, int]:
    if isinstance(error, ConnectionRefusedError):
        return _REFUSED_UPSTREAM, _CONNECTION_REFUSED
    if isinstance(error, TimeoutError):
        return _TIMED_OUT_UPSTREAM, _TTL_EXPIRED
    return _UNREACHABLE_UPSTREAM, _HOST_UNREACHABLE
