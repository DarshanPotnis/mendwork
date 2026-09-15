"""The egress policy for runs against servers on this machine (ADR 0011).

Benchmarks and browser tests replay workflows against the chaos portal and fixture sites served on
ephemeral loopback ports. Each such origin must be a loopback exception, or the run is refused; this
builds exactly those exceptions from the run's own URLs, and nothing else. A URL that is not a
literal loopback origin adds nothing, so a benchmark can never widen a policy to a real site.
"""

from collections.abc import Iterable

from mendwork.engine.safety.egress import (
    EgressPolicy,
    LoopbackException,
    UrlTarget,
    read_url,
)


def local_policy(values: Iterable[str], *, domains: Iterable[str] = ()) -> EgressPolicy:
    """Loopback exceptions for every literal loopback origin among ``values``.

    Values that are not URLs (an email address, say) are skipped. ``domains`` are allowlisted as
    given, for fixture pages a test serves through Playwright routes under a made-up name.
    """
    exceptions: dict[tuple[str, int], LoopbackException] = {}
    for value in values:
        target = read_url(value)
        if not isinstance(target, UrlTarget):
            continue
        address = target.host.address
        if address is None or not address.is_loopback:
            continue
        exceptions[(str(address), target.port)] = LoopbackException(
            address=str(address), port=target.port
        )
    return EgressPolicy(
        allowed_domains=tuple(domains), loopback_exceptions=tuple(exceptions.values())
    )
