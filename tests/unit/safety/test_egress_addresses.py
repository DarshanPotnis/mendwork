"""Internal, special, and metadata addresses are refused; hosts are read as browsers read them."""

import ipaddress

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.safety.egress_addresses import (
    AddressRange,
    Host,
    blocked_range,
    parse_address,
    parse_host,
)

UNSPECIFIED_V4 = "0.0.0.0"  # noqa: S104 - an address under test, never bound to

REFUSED = [
    # Loopback, both families, both ends of 127/8.
    ("127.0.0.1", AddressRange.LOOPBACK),
    ("127.255.255.254", AddressRange.LOOPBACK),
    ("::1", AddressRange.LOOPBACK),
    # RFC 1918 and unique local, at their edges.
    ("10.0.0.0", AddressRange.PRIVATE),
    ("10.255.255.255", AddressRange.PRIVATE),
    ("172.16.0.0", AddressRange.PRIVATE),
    ("172.31.255.255", AddressRange.PRIVATE),
    ("192.168.0.1", AddressRange.PRIVATE),
    ("fc00::1", AddressRange.PRIVATE),
    ("fdff:ffff:ffff::1", AddressRange.PRIVATE),
    # Link-local.
    ("169.254.0.1", AddressRange.LINK_LOCAL),
    ("169.254.255.254", AddressRange.LINK_LOCAL),
    ("fe80::1", AddressRange.LINK_LOCAL),
    # Carrier-grade NAT, which Python calls neither private nor global.
    ("100.64.0.0", AddressRange.SHARED),
    ("100.127.255.255", AddressRange.SHARED),
    # Unspecified and multicast (Python calls multicast global).
    (UNSPECIFIED_V4, AddressRange.UNSPECIFIED),
    ("::", AddressRange.UNSPECIFIED),
    ("224.0.0.1", AddressRange.MULTICAST),
    ("239.255.255.255", AddressRange.MULTICAST),
    ("ff02::1", AddressRange.MULTICAST),
    # Other special-purpose space.
    ("0.1.2.3", AddressRange.SPECIAL_PURPOSE),
    ("192.0.2.1", AddressRange.SPECIAL_PURPOSE),
    ("198.51.100.1", AddressRange.SPECIAL_PURPOSE),
    ("203.0.113.1", AddressRange.SPECIAL_PURPOSE),
    ("198.18.0.1", AddressRange.SPECIAL_PURPOSE),
    ("240.0.0.1", AddressRange.SPECIAL_PURPOSE),
    ("255.255.255.255", AddressRange.SPECIAL_PURPOSE),
    ("2001:db8::1", AddressRange.SPECIAL_PURPOSE),
    ("64:ff9b:1::1", AddressRange.SPECIAL_PURPOSE),
    # Cloud metadata endpoints, including Azure's publicly routable one.
    ("169.254.169.254", AddressRange.CLOUD_METADATA),
    ("169.254.170.2", AddressRange.CLOUD_METADATA),
    ("fd00:ec2::254", AddressRange.CLOUD_METADATA),
    ("100.100.100.200", AddressRange.CLOUD_METADATA),
    ("192.0.0.192", AddressRange.CLOUD_METADATA),
    ("168.63.129.16", AddressRange.CLOUD_METADATA),
    # IPv4 embedded in IPv6 is refused for what it carries.
    ("::ffff:127.0.0.1", AddressRange.LOOPBACK),
    ("::ffff:a9fe:a9fe", AddressRange.CLOUD_METADATA),
    ("::7f00:1", AddressRange.LOOPBACK),
    ("2002:a9fe:a9fe::1", AddressRange.CLOUD_METADATA),
    ("64:ff9b::a9fe:a9fe", AddressRange.CLOUD_METADATA),
    ("64:ff9b::a00:1", AddressRange.PRIVATE),
    ("2001:0:4136:e378:8000:63bf:80ff:fffe", AddressRange.LOOPBACK),
]

ALLOWED = [
    "8.8.8.8",
    "1.1.1.1",
    "11.0.0.1",
    "172.15.255.255",
    "172.32.0.0",
    "100.63.255.255",
    "100.128.0.0",
    "169.253.255.255",
    "2606:4700:4700::1111",
    "::ffff:8.8.8.8",
]

BLOCKED_NETWORKS = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "0.0.0.0/8",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
]
BLOCKED_IPV4_NETWORKS = [network for network in BLOCKED_NETWORKS if "." in network]


@pytest.mark.parametrize(("address", "expected"), REFUSED)
def test_every_blocked_range_and_metadata_endpoint_is_refused(
    address: str, expected: AddressRange
) -> None:
    assert blocked_range(ipaddress.ip_address(address)) is expected


@pytest.mark.parametrize("address", ALLOWED)
def test_globally_routable_addresses_next_to_blocked_ranges_are_allowed(address: str) -> None:
    assert blocked_range(ipaddress.ip_address(address)) is None


def test_a_nat64_address_is_refused_even_when_it_carries_a_public_address() -> None:
    # Python does not count 64:ff9b::/96 as globally routable, and refusing is the safe reading:
    # an IPv6-only network that needs NAT64 to reach a site is outside what a run should assume.
    assert blocked_range(ipaddress.ip_address("64:ff9b::808:808")) is not None


@given(st.sampled_from(BLOCKED_NETWORKS).flatmap(lambda net: st.ip_addresses(network=net)))
def test_no_address_inside_a_blocked_network_is_ever_allowed(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> None:
    assert blocked_range(address) is not None


@given(st.sampled_from(BLOCKED_IPV4_NETWORKS).flatmap(lambda net: st.ip_addresses(network=net)))
def test_a_blocked_ipv4_address_stays_blocked_in_every_ipv6_wrapping(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> None:
    value = int(address)
    wrappings = [
        ipaddress.IPv6Address((0xFFFF << 32) | value),
        ipaddress.IPv6Address((0x2002 << 112) | (value << 80)),
        ipaddress.IPv6Address((0x64FF9B << 96) | value),
    ]
    assert all(blocked_range(wrapped) is not None for wrapped in wrappings)


@pytest.mark.parametrize(
    "host",
    ["2130706433", "0x7f000001", "0177.0.0.1", "127.1", "0x7f.1", "0X7F.0.0.1", "127.0.0.1.",
     "%31%32%37.0.0.1", "017700000001"],
)  # fmt: skip
def test_numeric_host_forms_are_read_as_the_address_a_browser_reads(host: str) -> None:
    parsed = parse_host(host)

    assert parsed == Host("127.0.0.1", ipaddress.IPv4Address("127.0.0.1"))


def test_names_are_lower_cased_without_a_trailing_dot_and_converted_to_ascii() -> None:
    assert parse_host("Portal.EXAMPLE.com.") == Host("portal.example.com", None)
    assert parse_host("bücher.example") == Host("xn--bcher-kva.example", None)
    assert parse_host("0x.example") == Host("0x.example", None)


def test_ipv6_hosts_are_canonical_and_bracketed_in_their_label() -> None:
    parsed = parse_host("[0:0::1]")

    assert parsed == Host("::1", ipaddress.IPv6Address("::1"))
    assert parsed.label == "[::1]"


@pytest.mark.parametrize(
    "host",
    ["", ".", "256.0.0.1", "1.2.3.4.5", "1.2.3.09", "0x1g.0.0.1", "exa mple.com", "a<b.test",
     "[::1", "[fe80::1%25en0]", "[not-v6]", "%ff.example"],
)  # fmt: skip
def test_hosts_a_browser_would_refuse_are_refused(host: str) -> None:
    with pytest.raises(ValueError, match=r"host|IPv6"):
        parse_host(host)


def test_resolved_addresses_lose_their_zone() -> None:
    assert parse_address("fe80::1%en0") == ipaddress.IPv6Address("fe80::1")
    assert parse_address("10.0.0.1") == ipaddress.IPv4Address("10.0.0.1")
