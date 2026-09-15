"""The egress policy: schemes, URL shapes, the allowlist, loopback exceptions, and resolution."""

import ipaddress

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from mendwork.engine.safety.egress import (
    EgressPolicy,
    EgressRule,
    LoopbackException,
    UrlTarget,
    check_connection,
    check_navigation,
    parse_domain_pattern,
    parse_loopback_exception,
    read_url,
)
from mendwork.engine.safety.egress_addresses import AddressRange, parse_address

POLICY = EgressPolicy(
    allowed_domains=("example.com", "*.corp.example", "bücher.example"),
    loopback_exceptions=(LoopbackException(address="127.0.0.1", port=8765),),
)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "data:text/html,<p>x</p>", "javascript:alert(1)", "ftp://example.com/",
     "blob:https://example.com/0f1e", "chrome://settings", "about:blank", "ws://example.com/",
     "example.com/path"],
)  # fmt: skip
def test_every_non_http_scheme_is_refused_in_every_frame(url: str) -> None:
    for main_frame in (True, False):
        refusal = check_navigation(url, POLICY, main_frame=main_frame)
        assert refusal is not None
        assert refusal.rule is EgressRule.SCHEME


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        ("https://user:pw@example.com/", "credentials"),
        ("https://example.com\\@evil.test/", "backslash"),
        ("https://exa mple.com/", "whitespace"),
        ("https://example.com/\n", "whitespace"),
        ("http:///path", "no host"),
        ("https://example.com:99999/", "invalid port"),
        ("https://example.com:0/", "invalid port"),
        ("https://[::1/", "cannot be read"),
        ("https://[::1]x/", "cannot be read"),
    ],
)
def test_urls_a_browser_could_read_differently_are_refused(url: str, detail: str) -> None:
    refusal = check_navigation(url, POLICY, main_frame=True)

    assert refusal is not None
    assert refusal.rule is EgressRule.MALFORMED_URL
    assert detail in refusal.detail


@pytest.mark.parametrize(
    "url",
    ["https://example.com/reports?x=1", "https://EXAMPLE.com./", "http://example.com:8080/",
     "https://a.corp.example/", "https://a.b.corp.example/", "https://bücher.example/",
     "https://xn--bcher-kva.example/"],
)  # fmt: skip
def test_allowlisted_hosts_may_be_navigated_to(url: str) -> None:
    assert check_navigation(url, POLICY, main_frame=True) is None


@pytest.mark.parametrize(
    "url",
    ["https://www.example.com/", "https://corp.example/", "https://example.com.evil.test/",
     "https://evilexample.com/", "https://acorp.example/", "http://8.8.8.8/",
     "http://localhost:8765/"],
)  # fmt: skip
def test_hosts_off_the_allowlist_are_refused_for_top_level_navigation(url: str) -> None:
    refusal = check_navigation(url, POLICY, main_frame=True)

    assert refusal is not None
    assert refusal.rule is EgressRule.NOT_ALLOWLISTED
    assert "?" not in refusal.detail


def test_frames_are_held_to_scheme_and_address_rules_but_not_the_allowlist() -> None:
    assert check_navigation("https://ads.example.net/embed", POLICY, main_frame=False) is None
    refusal = check_navigation("http://169.254.169.254/latest/", POLICY, main_frame=False)
    assert refusal is not None
    assert refusal.address_range is AddressRange.CLOUD_METADATA


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://10.0.0.5/", AddressRange.PRIVATE),
        ("http://[::1]:8765/", AddressRange.LOOPBACK),
        ("http://127.0.0.1:8766/", AddressRange.LOOPBACK),
        ("http://0x7f.1:9/", AddressRange.LOOPBACK),
        ("http://[::ffff:a9fe:a9fe]/", AddressRange.CLOUD_METADATA),
    ],
)
def test_literal_addresses_are_refused_by_range_before_the_allowlist(
    url: str, expected: AddressRange
) -> None:
    refusal = check_navigation(url, POLICY, main_frame=True)

    assert refusal is not None
    assert (refusal.rule, refusal.address_range) == (EgressRule.BLOCKED_ADDRESS, expected)


def test_a_loopback_exception_admits_exactly_its_address_and_port() -> None:
    assert check_navigation("http://127.0.0.1:8765/index.html", POLICY, main_frame=True) is None
    assert check_navigation("http://2130706433:8765/index.html", POLICY, main_frame=True) is None
    assert check_connection("127.0.0.1", 8765, (), POLICY) is None
    refused = check_connection("127.0.0.1", 5432, (), POLICY)
    assert refused is not None
    assert refused.address_range is AddressRange.LOOPBACK


def test_a_name_that_resolves_to_a_private_address_is_refused() -> None:
    addresses = (parse_address("8.8.8.8"), parse_address("10.0.0.5"))

    refusal = check_connection("rebind.example.com", 443, addresses, POLICY)

    assert refusal is not None
    assert (refusal.rule, refusal.address, refusal.address_range) == (
        EgressRule.BLOCKED_ADDRESS,
        "10.0.0.5",
        AddressRange.PRIVATE,
    )
    assert refusal.detail == "rebind.example.com resolves to 10.0.0.5, which is a private address"


def test_a_name_resolving_to_the_excepted_loopback_address_is_still_refused() -> None:
    refusal = check_connection("localhost", 8765, (parse_address("127.0.0.1"),), POLICY)

    assert refusal is not None
    assert refusal.address_range is AddressRange.LOOPBACK


def test_a_name_whose_every_address_is_public_may_be_connected_to() -> None:
    public = (parse_address("8.8.8.8"), parse_address("2606:4700:4700::1111"))

    assert check_connection("example.com", 443, public, POLICY) is None


def test_a_literal_address_is_checked_as_itself_whatever_else_is_passed() -> None:
    refusal = check_connection("10.1.2.3", 80, (parse_address("8.8.8.8"),), POLICY)

    assert refusal is not None
    assert refusal.address == "10.1.2.3"


def test_an_unreadable_requested_host_is_refused_as_malformed() -> None:
    refusal = check_connection("exa mple", 80, (), POLICY)

    assert refusal is not None
    assert refusal.rule is EgressRule.MALFORMED_URL


def test_urls_are_read_into_scheme_host_and_default_ports() -> None:
    https = read_url("https://example.com/x")
    ipv6 = read_url("http://[2606:4700::1]:8080/")

    assert isinstance(https, UrlTarget)
    assert isinstance(ipv6, UrlTarget)
    assert (https.scheme, https.host.text, https.port) == ("https", "example.com", 443)
    assert (ipv6.host.label, ipv6.port) == ("[2606:4700::1]", 8080)


def test_nothing_is_allowlisted_by_default() -> None:
    refusal = check_navigation("https://example.com/", EgressPolicy(), main_frame=True)

    assert refusal is not None
    assert refusal.rule is EgressRule.NOT_ALLOWLISTED


@pytest.mark.parametrize(
    ("pattern", "canonical"),
    [
        ("Example.COM.", "example.com"),
        ("*.Corp.Example", "*.corp.example"),
        ("bücher.example", "xn--bcher-kva.example"),
        (" intranet_app.example ", "intranet_app.example"),
    ],
)
def test_allowlist_entries_are_normalized(pattern: str, canonical: str) -> None:
    assert parse_domain_pattern(pattern) == canonical


@pytest.mark.parametrize(
    "pattern", ["", "*", "*.com", "a.*.example", "*example.com", "10.0.0.1", "0x7f.1",
                "exa mple.com", "-bad.example", "bad-.example"],
)  # fmt: skip
def test_allowlist_entries_that_are_not_host_names_are_refused(pattern: str) -> None:
    with pytest.raises(ValueError, match="allowlist entry"):
        parse_domain_pattern(pattern)


@given(
    base=st.from_regex(r"[a-z][a-z0-9]{0,8}\.[a-z]{2,6}", fullmatch=True),
    label=st.from_regex(r"[a-z0-9]{1,10}", fullmatch=True),
)
def test_a_wildcard_admits_subdomains_but_never_the_name_itself_and_an_exact_name_the_reverse(
    base: str, label: str
) -> None:
    wildcard = EgressPolicy(allowed_domains=(f"*.{base}",))
    exact = EgressPolicy(allowed_domains=(base,))

    assert check_navigation(f"https://{label}.{base}/", wildcard, main_frame=True) is None
    assert check_navigation(f"https://{base}/", wildcard, main_frame=True) is not None
    assert check_navigation(f"https://{base}/", exact, main_frame=True) is None
    assert check_navigation(f"https://{label}.{base}/", exact, main_frame=True) is not None
    assert check_navigation(f"https://{label}{base}/", wildcard, main_frame=True) is not None


@pytest.mark.parametrize(
    ("text", "origin"),
    [
        ("127.0.0.1:8765", "127.0.0.1:8765"),
        ("[::1]:8765", "[::1]:8765"),
        ("127.0.0.2:9", "127.0.0.2:9"),
    ],
)
def test_loopback_exceptions_are_literal_loopback_origins_with_a_port(
    text: str, origin: str
) -> None:
    assert parse_loopback_exception(text).origin == origin


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("127.0.0.1", "must look like"),
        ("127.0.0.1:", "needs a port"),
        ("localhost:8765", "literal IP address"),
        ("10.0.0.1:80", "loopback address"),
        ("169.254.169.254:80", "loopback address"),
        ("0.0.0.0:80", "loopback address"),
        ("127.0.0.1:0", "invalid port"),
        ("127.0.0.1:65536", "invalid port"),
        ("*:8765", "literal IP address"),
        ("127.0.0.0/8:80", "literal IP address"),
        ("::1:8765", "must look like"),
        ("[::1]8765", "must look like"),
        ("[0:0::1]:8765", "canonically"),
    ],
)
def test_loopback_exceptions_that_could_open_anything_else_are_refused(
    text: str, reason: str
) -> None:
    with pytest.raises(ValueError, match="loopback exception") as caught:
        parse_loopback_exception(text)

    assert reason in str(caught.value)


def test_a_policy_cannot_be_built_with_a_non_loopback_exception_or_a_bad_domain() -> None:
    with pytest.raises(ValidationError):
        LoopbackException(address="10.0.0.1", port=80)
    with pytest.raises(ValidationError):
        EgressPolicy(allowed_domains=("*.com",))
    assert ipaddress.ip_address(POLICY.loopback_exceptions[0].address).is_loopback
