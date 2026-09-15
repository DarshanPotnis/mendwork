"""Egress settings: where runs may navigate, and the loopback origins local tests use (ADR 0011).

A base of ``Settings``, like ``settings_model``. Nothing is allowlisted by default, so a run
navigates nowhere until a person names the sites it may automate.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings

from mendwork.engine.safety.egress import (
    EgressPolicy,
    parse_domain_pattern,
    parse_loopback_exception,
)


class EgressSettings(BaseSettings):
    """The egress policy every run is held to."""

    # Host names a run may navigate to at the top level: exact names, or *.example.com for any
    # subdomain. Empty allows no navigation at all: sites are automated only once named.
    egress_allowed_domains: frozenset[str] = frozenset()
    # Exact loopback ip:port origins exempt from the address rules, for local test targets such as
    # the chaos portal (127.0.0.1:8765). Only literal loopback addresses with a port are accepted,
    # and Settings refuses any of them in production.
    egress_loopback_exceptions: frozenset[str] = frozenset()

    @field_validator("egress_allowed_domains")
    @classmethod
    def _domains_are_patterns(cls, value: frozenset[str]) -> frozenset[str]:
        return frozenset(parse_domain_pattern(item) for item in value)

    @field_validator("egress_loopback_exceptions")
    @classmethod
    def _exceptions_are_loopback_origins(cls, value: frozenset[str]) -> frozenset[str]:
        return frozenset(parse_loopback_exception(item).origin for item in value)

    def egress_policy(self) -> EgressPolicy:
        """The policy runs are held to, in a stable order."""
        return EgressPolicy(
            allowed_domains=tuple(sorted(self.egress_allowed_domains)),
            loopback_exceptions=tuple(
                parse_loopback_exception(origin)
                for origin in sorted(self.egress_loopback_exceptions)
            ),
        )
