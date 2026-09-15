"""Egress settings: deny by default, normalized entries, no loopback exceptions in production."""

import pytest
from pydantic import ValidationError

from mendwork.engine.safety.egress import EgressPolicy, LoopbackException
from mendwork.settings import Environment, Settings


def test_nothing_is_allowlisted_or_excepted_by_default() -> None:
    assert Settings(_env_file=None).egress_policy() == EgressPolicy()


def test_entries_from_the_environment_are_normalized_into_a_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MENDWORK_EGRESS_ALLOWED_DOMAINS", '["Portal.Example.com", "*.Corp.Example"]'
    )
    monkeypatch.setenv("MENDWORK_EGRESS_LOOPBACK_EXCEPTIONS", '["127.0.0.1:8765", "[::1]:9000"]')

    policy = Settings(_env_file=None).egress_policy()

    assert policy == EgressPolicy(
        allowed_domains=("*.corp.example", "portal.example.com"),
        loopback_exceptions=(
            LoopbackException(address="127.0.0.1", port=8765),
            LoopbackException(address="::1", port=9000),
        ),
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("egress_allowed_domains", frozenset({"*.com"}), "at least two labels"),
        ("egress_allowed_domains", frozenset({"10.0.0.1"}), "is an IP address"),
        ("egress_loopback_exceptions", frozenset({"10.0.0.1:80"}), "loopback address"),
        ("egress_loopback_exceptions", frozenset({"localhost:8765"}), "literal IP address"),
    ],
)
def test_invalid_entries_are_refused_at_startup(
    field: str, value: frozenset[str], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        Settings.model_validate({"_env_file": None, field: value})


def test_production_refuses_any_loopback_exception() -> None:
    with pytest.raises(ValidationError, match="must be empty in production"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            egress_loopback_exceptions=frozenset({"127.0.0.1:8765"}),
        )


def test_production_accepts_an_allowlist_without_exceptions() -> None:
    settings = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        egress_allowed_domains=frozenset({"portal.example.com"}),
    )

    assert settings.egress_policy().allowed_domains == ("portal.example.com",)
