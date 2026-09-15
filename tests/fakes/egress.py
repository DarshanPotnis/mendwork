"""A scripted HostResolver, and the egress policy engine tests replay under."""

from collections.abc import Mapping
from typing import Final

from mendwork.engine.errors import NavigationError
from mendwork.engine.replay.navigation_guard import NavigationGuard
from mendwork.engine.safety.egress import EgressPolicy

PUBLIC_ADDRESS: Final = "93.184.215.14"
"""An address a public site could have; every name resolves to it unless a test says otherwise."""
TEST_POLICY: Final = EgressPolicy(
    allowed_domains=("*.example.test", "example.test", "a.test", "x.test", "elsewhere.test")
)
"""The allowlist the fake pages' hosts are on."""


class FakeResolver:
    """Answers from a table, recording every lookup; unknown names get a public address."""

    def __init__(
        self,
        answers: Mapping[str, tuple[str, ...]] | None = None,
        *,
        failure: NavigationError | None = None,
    ) -> None:
        self._answers = dict(answers or {})
        self._failure = failure
        self.lookups: list[str] = []

    async def resolve(self, host: str, *, timeout_ms: int) -> tuple[str, ...]:
        self.lookups.append(host)
        if self._failure is not None:
            raise self._failure
        return self._answers.get(host, (PUBLIC_ADDRESS,))


def navigation_guard(
    policy: EgressPolicy = TEST_POLICY, resolver: FakeResolver | None = None
) -> NavigationGuard:
    """A guard for engine tests: the fake pages' hosts are allowed and resolve publicly."""
    return NavigationGuard(policy=policy, resolver=resolver or FakeResolver())
