"""The test suite cannot reach the network, and can still reach this machine."""

import socket

import pytest

from tests.conftest import NetworkAccessBlockedError, is_local_address


def test_a_connection_to_another_host_is_refused() -> None:
    with pytest.raises(NetworkAccessBlockedError):
        socket.create_connection(("192.0.2.1", 80), timeout=0.1)


def test_a_public_name_cannot_be_resolved() -> None:
    with pytest.raises(NetworkAccessBlockedError):
        socket.getaddrinfo("example.com", 443)


def test_loopback_still_works() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=1):
            accepted, _ = server.accept()
            accepted.close()


@pytest.mark.parametrize(
    ("address", "local"),
    [
        (("127.0.0.1", 80), True),
        (("::1", 80, 0, 0), True),
        (("localhost", 80), True),
        ("mendwork.sock", True),
        (("10.0.0.1", 80), False),
        (("models.example.test", 443), False),
        ((), False),
    ],
)
def test_only_loopback_hosts_and_unix_sockets_are_local(address: object, local: bool) -> None:
    assert is_local_address(address) is local
