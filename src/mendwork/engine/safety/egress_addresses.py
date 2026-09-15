"""Which network addresses a run's browser may reach, and how a URL's host is read.

A browser that a workflow points somewhere must never reach the machine it runs on, the network
around that machine, or a cloud provider's metadata service: that is how a forged request turns
an automation bot into a way in. So an address is refused when it is not globally routable, when
it is one of the named metadata endpoints (one of them, Azure's, is publicly routable), and when
it embeds a refused IPv4 address (IPv4-mapped or -compatible, 6to4, Teredo, NAT64).

Hosts are read the way a browser reads them (the WHATWG URL standard): ``2130706433``,
``0x7f.1``, and ``127.1`` are all 127.0.0.1, so they are checked as addresses and never looked
up as names. The rules live here rather than in Settings because they define what "internal"
means; no setting may shrink them.
"""

import ipaddress
import string
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import unquote

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class AddressRange(StrEnum):
    """Why an address is refused."""

    CLOUD_METADATA = "cloud_metadata"
    """A cloud provider's instance metadata or host service."""
    UNSPECIFIED = "unspecified"
    LOOPBACK = "loopback"
    LINK_LOCAL = "link_local"
    MULTICAST = "multicast"
    PRIVATE = "private"
    """RFC 1918 networks and IPv6 unique local addresses."""
    SHARED = "shared"
    """Carrier-grade NAT space, 100.64.0.0/10."""
    SPECIAL_PURPOSE = "special_purpose"
    """Any other address that is not globally routable: documentation, benchmarking, reserved."""


CLOUD_METADATA_ADDRESSES: Final[frozenset[IPAddress]] = frozenset(
    ipaddress.ip_address(address)
    for address in (
        "169.254.169.254",  # AWS, Google Cloud, Azure, OpenStack, and DigitalOcean metadata
        "169.254.170.2",  # AWS ECS task metadata and credentials
        "fd00:ec2::254",  # AWS instance metadata over IPv6
        "100.100.100.200",  # Alibaba Cloud instance metadata
        "192.0.0.192",  # Oracle Cloud Infrastructure instance metadata
        "168.63.129.16",  # Azure WireServer, publicly routable, so it must be named
    )
)
_PRIVATE_NETWORKS: Final = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_SHARED_NETWORK: Final = ipaddress.ip_network("100.64.0.0/10")
_NAT64_NETWORK: Final = ipaddress.ip_network("64:ff9b::/96")
_IPV4_MAX: Final = (1 << 32) - 1
_IPV4_PARTS_MAX: Final = 4
_IPV4_PART_MAX: Final = 255
_HOST_FORBIDDEN: Final = frozenset(" #%/:<>?@[\\]^|")
_DEL: Final = 0x7F
_FIRST_PRINTABLE: Final = 0x20
_RADIX_DIGITS: Final = {10: string.digits, 16: string.hexdigits, 8: string.octdigits}


def blocked_range(address: IPAddress) -> AddressRange | None:
    """Why the address is refused, or None when it is globally routable.

    An IPv6 address that carries an IPv4 address is refused for what it carries, whatever the
    IPv6 address itself would be.
    """
    for embedded in _embedded_ipv4(address):
        found = _own_range(embedded)
        if found is not None:
            return found
    return _own_range(address)


def _own_range(address: IPAddress) -> AddressRange | None:
    if address in CLOUD_METADATA_ADDRESSES:
        return AddressRange.CLOUD_METADATA
    if address.is_unspecified:
        return AddressRange.UNSPECIFIED
    if address.is_loopback:
        return AddressRange.LOOPBACK
    if address.is_link_local:
        return AddressRange.LINK_LOCAL
    # Python counts multicast as global; a browser has no business sending to a group.
    if address.is_multicast:
        return AddressRange.MULTICAST
    if any(address in network for network in _PRIVATE_NETWORKS):
        return AddressRange.PRIVATE
    if address in _SHARED_NETWORK:
        return AddressRange.SHARED
    if address.is_reserved or not address.is_global:
        return AddressRange.SPECIAL_PURPOSE
    return None


def _embedded_ipv4(address: IPAddress) -> tuple[ipaddress.IPv4Address, ...]:
    if isinstance(address, ipaddress.IPv4Address):
        return ()
    found: list[ipaddress.IPv4Address] = []
    if address.ipv4_mapped is not None:
        found.append(address.ipv4_mapped)
    if address.sixtofour is not None:
        found.append(address.sixtofour)
    if address.teredo is not None:
        found.extend(address.teredo)
    if address in _NAT64_NETWORK:
        found.append(ipaddress.IPv4Address(int(address) & _IPV4_MAX))
    # IPv4-compatible (::a.b.c.d), deprecated but still routed by some stacks.
    if 1 < int(address) <= _IPV4_MAX:
        found.append(ipaddress.IPv4Address(int(address)))
    return tuple(found)


def parse_address(text: str) -> IPAddress:
    """An address a resolver returned, without any IPv6 zone. Raises ValueError."""
    return ipaddress.ip_address(text.split("%", 1)[0])


@dataclass(frozen=True, slots=True)
class Host:
    """A URL's host as a browser reads it: an address, or a lower-case ASCII name."""

    text: str
    """The canonical address, or the name without a trailing dot."""
    address: IPAddress | None

    @property
    def label(self) -> str:
        """The host as it appears in a URL, with brackets around an IPv6 address."""
        if isinstance(self.address, ipaddress.IPv6Address):
            return f"[{self.text}]"
        return self.text


def parse_host(raw: str) -> Host:
    """Read a URL's host as the WHATWG URL standard does. Raises ValueError when it is invalid."""
    if raw.startswith("["):
        return _ipv6_host(raw)
    try:
        decoded = unquote(raw, errors="strict")
    except UnicodeDecodeError:
        raise ValueError("has an invalid percent-encoded host") from None
    name = _ascii_name(decoded)
    if not name or name == ".":
        raise ValueError("has no host")
    if any(
        character in _HOST_FORBIDDEN or ord(character) < _FIRST_PRINTABLE or ord(character) == _DEL
        for character in name
    ):
        raise ValueError("has a host with characters a host name cannot contain")
    if _ends_in_number(name):
        address = _parse_ipv4(name)
        return Host(str(address), address)
    return Host(name.removesuffix("."), None)


def _ipv6_host(raw: str) -> Host:
    if not raw.endswith("]"):
        raise ValueError("has an unterminated IPv6 address")
    inner = raw[1:-1]
    if "%" in inner:
        raise ValueError("has an IPv6 zone, which a URL cannot carry")
    try:
        address = ipaddress.IPv6Address(inner)
    except ValueError:
        raise ValueError("has a malformed IPv6 address") from None
    return Host(str(address), address)


def _ascii_name(decoded: str) -> str:
    if decoded.isascii():
        return decoded.lower()
    try:
        return decoded.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("has an invalid internationalized host name") from None


def _ends_in_number(name: str) -> bool:
    parts = name.split(".")
    if parts[-1] == "":
        if len(parts) == 1:
            return False
        parts = parts[:-1]
    last = parts[-1]
    if last and all(character in string.digits for character in last):
        return True
    return _ipv4_number(last) is not None


def _ipv4_number(part: str) -> int | None:
    if not part:
        return None
    radix = 10
    if part[:2] in {"0x", "0X"}:
        part, radix = part[2:], 16
    elif len(part) > 1 and part[0] == "0":
        part, radix = part[1:], 8
    if not part:
        return 0
    if any(character not in _RADIX_DIGITS[radix] for character in part):
        return None
    return int(part, radix)


def _parse_ipv4(name: str) -> ipaddress.IPv4Address:
    parts = name.split(".")
    if parts[-1] == "" and len(parts) > 1:
        parts = parts[:-1]
    numbers = [_ipv4_number(part) for part in parts]
    valid = [number for number in numbers if number is not None]
    if len(parts) > _IPV4_PARTS_MAX or len(valid) != len(numbers):
        raise ValueError("has a host that is not a valid IPv4 address")
    if any(number > _IPV4_PART_MAX for number in valid[:-1]):
        raise ValueError("has a host that is not a valid IPv4 address")
    if valid[-1] >= 256 ** (5 - len(valid)):
        raise ValueError("has a host that is not a valid IPv4 address")
    value = valid[-1] + sum(number * 256 ** (3 - index) for index, number in enumerate(valid[:-1]))
    return ipaddress.IPv4Address(value)
