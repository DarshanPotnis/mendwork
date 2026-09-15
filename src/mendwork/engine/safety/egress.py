"""The egress policy: where a run's browser may go, and what it may connect to (ADR 0011).

Two questions, each answered by a pure function:

- **May the browser navigate to this URL?** A top-level navigation needs an http or https URL
  with nothing ambiguous in it (credentials, backslashes, whitespace) and a host on the run's
  allowlist: an exact name, or ``*.example.com`` for any subdomain. Nothing is allowlisted by
  default. A frame inside the page is held to the scheme and address rules but not to the
  allowlist, because embedded content comes from third parties.
- **May the browser connect to these addresses?** Every address a host resolves to must pass
  the address rules (``egress_addresses``). One refused address refuses the host, because the
  browser could connect to any of them.

The one exception is an exact loopback ``ip:port`` named in the policy, for local test targets
such as the chaos portal. It must be a literal loopback address with a port. It exempts exactly
that origin from the address rules and counts as allowlisted; a host name that resolves to
loopback, another port, and every other range stay refused. Settings refuses exceptions in
production.
"""

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, model_validator

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.safety.egress_addresses import (
    AddressRange,
    Host,
    IPAddress,
    blocked_range,
    parse_host,
)

_DEFAULT_PORTS: Final = {"http": 80, "https": 443}
_PORT: Final = re.compile(r"[0-9]{1,5}")
_PORT_MAX: Final = 65_535
_LABEL: Final = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")
_WILDCARD: Final = "*."
_WILDCARD_LABELS_MIN: Final = 2
_DEL: Final = 0x7F
_FIRST_PRINTABLE: Final = 0x20


class EgressRule(StrEnum):
    """Which rule refused a navigation or a connection."""

    SCHEME = "scheme"
    """Only http and https may be navigated to."""
    MALFORMED_URL = "malformed_url"
    """The URL carries credentials, a backslash, whitespace, or a host that cannot be read."""
    NOT_ALLOWLISTED = "not_allowlisted"
    """The host is not on the run's allowlist of domains."""
    BLOCKED_ADDRESS = "blocked_address"
    """The host is, or resolves to, an internal or metadata address."""


def parse_domain_pattern(text: str) -> str:
    """An allowlist entry in canonical form: ``example.com`` or ``*.example.com``.

    Raises ValueError for an IP address, a wildcard anywhere but the front, a wildcard over a
    single label, or a name that is not a valid host name.
    """
    value = text.strip().lower()
    wildcard = value.startswith(_WILDCARD)
    base = value.removeprefix(_WILDCARD) if wildcard else value
    if "*" in base:
        raise ValueError(f"allowlist entry {text!r}: '*' is only allowed as a leading '*.'")
    try:
        host = parse_host(base)
    except ValueError as error:
        raise ValueError(f"allowlist entry {text!r} {error}") from None
    if host.address is not None:
        raise ValueError(
            f"allowlist entry {text!r} is an IP address; allowlist host names, and name a local "
            "test target in MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS instead"
        )
    labels = host.text.split(".")
    if any(_LABEL.fullmatch(label) is None for label in labels):
        raise ValueError(f"allowlist entry {text!r} is not a valid host name")
    if wildcard and len(labels) < _WILDCARD_LABELS_MIN:
        raise ValueError(
            f"allowlist entry {text!r}: a wildcard must cover a name of at least two labels, "
            "such as *.example.com"
        )
    return f"{_WILDCARD}{host.text}" if wildcard else host.text


def _check_domain_patterns(patterns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(parse_domain_pattern(pattern) for pattern in patterns)


class LoopbackException(DomainModel):
    """One loopback origin exempt from the address rules, such as the local chaos portal."""

    address: str
    """A literal loopback address in canonical form: 127.0.0.0/8 or ::1."""
    port: int = Field(ge=1, le=_PORT_MAX)

    @model_validator(mode="after")
    def _is_a_canonical_loopback_address(self) -> Self:
        problem = _loopback_problem(self.address)
        if problem is not None:
            raise ValueError(problem)
        return self

    @property
    def origin(self) -> str:
        """The exception as ``ip:port``, with brackets around an IPv6 address."""
        host = f"[{self.address}]" if ":" in self.address else self.address
        return f"{host}:{self.port}"


def parse_loopback_exception(text: str) -> LoopbackException:
    """A loopback exception from ``127.0.0.1:8765`` or ``[::1]:8765``. Raises ValueError."""
    value = text.strip()
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or value[end + 1 : end + 2] != ":":
            raise ValueError(f"loopback exception {text!r} must look like [::1]:8765")
        address, port = value[1:end], value[end + 2 :]
    else:
        address, separator, port = value.rpartition(":")
        if not separator or ":" in address:
            raise ValueError(f"loopback exception {text!r} must look like 127.0.0.1:8765")
    if _PORT.fullmatch(port) is None:
        raise ValueError(f"loopback exception {text!r} needs a port, such as 127.0.0.1:8765")
    if not 1 <= int(port) <= _PORT_MAX:
        raise ValueError(f"loopback exception {text!r} has an invalid port")
    problem = _loopback_problem(address)
    if problem is not None:
        raise ValueError(f"loopback exception {text!r} {problem}")
    return LoopbackException(address=address, port=int(port))


def _loopback_problem(text: str) -> str | None:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return "must be a literal IP address"
    if not address.is_loopback:
        return (
            "must be a loopback address (127.0.0.0/8 or ::1): an exception never opens private, "
            "link-local, or metadata addresses"
        )
    if str(address) != text:
        return f"must be written canonically, as {address}"
    return None


class EgressPolicy(DomainModel):
    """Where one run's browser may navigate, and the loopback origins it may reach."""

    allowed_domains: Annotated[tuple[str, ...], AfterValidator(_check_domain_patterns)] = ()
    loopback_exceptions: tuple[LoopbackException, ...] = ()


class EgressRefusal(DomainModel):
    """Why a navigation or a connection was refused, naming only the host, never the path."""

    rule: EgressRule
    host: str
    port: int | None = None
    address: str | None = None
    """The refused address, for an address rule."""
    address_range: AddressRange | None = None
    detail: str


@dataclass(frozen=True, slots=True)
class UrlTarget:
    """Where a URL leads: its scheme, host, and port."""

    scheme: str
    host: Host
    port: int


def read_url(url: str) -> UrlTarget | EgressRefusal:
    """A URL's scheme, host, and port, or why it cannot be navigated to at all.

    Anything a browser and this reading could disagree about (a backslash, whitespace, control
    characters, credentials) is refused rather than interpreted.
    """
    if any(
        character.isspace() or ord(character) < _FIRST_PRINTABLE or ord(character) == _DEL
        for character in url
    ):
        return _malformed("", "contains whitespace or control characters")
    if "\\" in url:
        return _malformed("", "contains a backslash, which browsers read as a slash")
    try:
        parts = urlsplit(url)
    except ValueError:
        return _malformed("", "cannot be read as a URL")
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        return EgressRefusal(
            rule=EgressRule.SCHEME,
            host="",
            detail=f"the URL uses the {scheme or 'missing'} scheme; only http and https may load",
        )
    if "@" in parts.netloc:
        return _malformed("", "carries credentials in its address")
    try:
        host_text, port_text = _split_netloc(parts.netloc)
        host = parse_host(host_text)
    except ValueError as error:
        return _malformed("", str(error))
    if port_text is None:
        return UrlTarget(scheme, host, _DEFAULT_PORTS[scheme])
    if _PORT.fullmatch(port_text) is None or not 1 <= int(port_text) <= _PORT_MAX:
        return _malformed(host.text, "has an invalid port")
    return UrlTarget(scheme, host, int(port_text))


def check_navigation(url: str, policy: EgressPolicy, *, main_frame: bool) -> EgressRefusal | None:
    """Why the browser may not navigate to this URL, or None when it may.

    A host name's addresses are not looked up here: the connection check does that, once, at
    the moment of connecting.
    """
    target = read_url(url)
    if isinstance(target, EgressRefusal):
        return target
    if is_loopback_exception(target.host, target.port, policy):
        return None
    if target.host.address is not None:
        refusal = _address_refusal(target.host, target.port, target.host.address)
        if refusal is not None:
            return refusal
    if main_frame and not domain_allowed(target.host, policy.allowed_domains):
        return EgressRefusal(
            rule=EgressRule.NOT_ALLOWLISTED,
            host=target.host.text,
            port=target.port,
            detail=f"{target.host.label} is not on the allowlist of domains this run may navigate",
        )
    return None


def check_connection(
    requested_host: str, port: int, addresses: Sequence[IPAddress], policy: EgressPolicy
) -> EgressRefusal | None:
    """Why the browser may not connect to a host, or None when every address it would use passes.

    ``requested_host`` is the host as the browser asked for it; ``addresses`` are what a name
    resolved to (ignored for a literal address, which is checked as itself).
    """
    try:
        host = parse_host(requested_host)
    except ValueError as error:
        return _malformed(requested_host, str(error))
    if is_loopback_exception(host, port, policy):
        return None
    for address in (host.address,) if host.address is not None else tuple(addresses):
        refusal = _address_refusal(host, port, address)
        if refusal is not None:
            return refusal
    return None


def domain_allowed(host: Host, patterns: Sequence[str]) -> bool:
    """Whether an allowlist admits a host name; an address is never admitted by name."""
    if host.address is not None:
        return False
    for pattern in patterns:
        if pattern.startswith(_WILDCARD):
            if host.text.endswith(pattern.removeprefix("*")):
                return True
        elif host.text == pattern:
            return True
    return False


def is_loopback_exception(host: Host, port: int, policy: EgressPolicy) -> bool:
    """Whether the host is literally one of the policy's loopback exceptions, port included."""
    if host.address is None:
        return False
    return any(
        exception.port == port and ipaddress.ip_address(exception.address) == host.address
        for exception in policy.loopback_exceptions
    )


def _address_refusal(host: Host, port: int, address: IPAddress) -> EgressRefusal | None:
    found = blocked_range(address)
    if found is None:
        return None
    kind = found.value.replace("_", " ")
    subject = (
        host.label if host.address is not None else f"{host.label} resolves to {address}, which"
    )
    return EgressRefusal(
        rule=EgressRule.BLOCKED_ADDRESS,
        host=host.text,
        port=port,
        address=str(address),
        address_range=found,
        detail=f"{subject} is a {kind} address",
    )


def _split_netloc(netloc: str) -> tuple[str, str | None]:
    if netloc.startswith("["):
        end = netloc.find("]")
        if end < 0:
            return netloc, None
        rest = netloc[end + 1 :]
        if not rest:
            return netloc[: end + 1], None
        if not rest.startswith(":"):
            raise ValueError("has characters after its IPv6 address")
        return netloc[: end + 1], rest[1:] or None
    host, separator, port = netloc.rpartition(":")
    if not separator:
        return netloc, None
    return host, port or None


def _malformed(host: str, detail: str) -> EgressRefusal:
    return EgressRefusal(rule=EgressRule.MALFORMED_URL, host=host, detail=f"the URL {detail}")
