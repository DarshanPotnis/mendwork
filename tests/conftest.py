"""Shared fixtures.

``configure_logging`` mutates process-wide state (structlog's defaults and the root
logger), so every test gets it put back to avoid order-dependent runs.
"""

import ipaddress
import logging
import socket
from collections.abc import Callable, Iterator

import click
import pytest
import structlog
from hypothesis import settings
from typer.testing import Result

# Deterministic property tests: the same examples on every run and machine, no example
# database carrying state between runs, and no wall-clock deadline to flake on slow CI.
settings.register_profile(
    "mendwork", derandomize=True, database=None, deadline=None, max_examples=100
)
settings.load_profile("mendwork")

_LOCAL_NAMES = frozenset({"localhost"})


class NetworkAccessBlockedError(RuntimeError):
    """A test tried to reach a host other than this machine."""


def is_local_address(address: object) -> bool:
    """Whether a socket address stays on this machine: a Unix socket path or a loopback host."""
    if isinstance(address, str | bytes):
        return True
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if host in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


LIVE_PROVIDERS_OPTION = "--live-providers"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        LIVE_PROVIDERS_OPTION,
        action="store_true",
        default=False,
        help="run only the tests that call the configured model provider (make live-providers)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Live provider tests run only when asked for, and then nothing else runs."""
    live = bool(config.getoption(LIVE_PROVIDERS_OPTION))
    kept = [item for item in items if (item.get_closest_marker("live") is not None) is live]
    dropped = [item for item in items if item not in kept]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


@pytest.fixture(autouse=True, scope="session")
def _no_network(pytestconfig: pytest.Config) -> Iterator[None]:
    """No test reaches the network: sockets may only connect to this machine.

    Model providers are exercised through recorded fixtures (respx), browsers only against the
    locally served portal. A test that tries anything else fails loudly instead of passing on a
    machine that happens to be online. Only ``make live-providers`` lifts this, because calling
    the configured provider is its whole purpose.
    """
    if pytestconfig.getoption(LIVE_PROVIDERS_OPTION):
        yield
        return
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: object) -> None:
        if not is_local_address(address):
            raise NetworkAccessBlockedError(f"tests may not connect to {address!r}")
        connect(self, address)  # type: ignore[arg-type]  # the address came from a real caller

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        if not is_local_address(address):
            raise NetworkAccessBlockedError(f"tests may not connect to {address!r}")
        return connect_ex(self, address)  # type: ignore[arg-type]  # as above

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        if host is not None and not is_local_address((host,)):
            raise NetworkAccessBlockedError(f"tests may not resolve {host!r}")
        return getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]  # passed through

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", guarded_connect)
        patch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
        patch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
        yield


@pytest.fixture
def plain_stdout() -> Callable[[Result], str]:
    """Return a CLI result's stdout with ANSI styling removed.

    Typer renders help through Rich, which emits styling whenever GITHUB_ACTIONS,
    FORCE_COLOR, or PY_COLORS is set — read at import time, so it cannot be turned off
    from inside a test run. Assertions match text, not the terminal's mood.
    """

    def strip_styling(result: Result) -> str:
        return click.unstyle(result.stdout)

    return strip_styling


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    try:
        yield
    finally:
        structlog.reset_defaults()
        root.handlers = handlers
        root.setLevel(level)
