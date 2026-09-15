"""Checking a URL against the run's egress policy before the browser is asked to load it.

The browser adapter enforces the same policy on every document request and every connection
(ADR 0011). This check runs first, so a refused URL fails with a precise reason and nothing is
sent at all. Every navigate step whose URL is known before the run starts is also checked in
preflight, so a workflow pointed at a refused site fails before a run exists.
"""

from collections.abc import Mapping, Sequence

from mendwork.engine.domain.identifiers import InputName
from mendwork.engine.domain.steps import NavigateStep
from mendwork.engine.domain.values import LiteralValue
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import NavigationError
from mendwork.engine.ports.resolver import HostResolver
from mendwork.engine.safety.egress import (
    EgressPolicy,
    UrlTarget,
    check_connection,
    check_navigation,
    read_url,
)
from mendwork.engine.safety.egress_addresses import IPAddress, parse_address
from mendwork.engine.safety.egress_blocks import EgressBlock, EgressLayer, egress_blocked

_NAME_NOT_RESOLVED = "ERR_NAME_RESOLUTION_FAILED"


class NavigationGuard:
    """One run's egress policy, checked before every navigation the engine starts."""

    def __init__(self, *, policy: EgressPolicy, resolver: HostResolver) -> None:
        self._policy = policy
        self._resolver = resolver

    @property
    def policy(self) -> EgressPolicy:
        """The policy this run is held to."""
        return self._policy

    async def check(self, url: str, *, timeout_ms: int) -> None:
        """Raise EgressBlocked when the policy refuses the URL, or any address its host resolves to.

        A host name that does not resolve raises NavigationError instead, which the navigation
        retry policy classifies like any other failed lookup.
        """
        refusal = check_navigation(url, self._policy, main_frame=True)
        if refusal is None:
            target = read_url(url)
            if isinstance(target, UrlTarget) and target.host.address is None:
                addresses = await self._resolve(target.host.text, timeout_ms)
                refusal = check_connection(target.host.text, target.port, addresses, self._policy)
        if refusal is not None:
            raise egress_blocked(
                [EgressBlock(layer=EgressLayer.NAVIGATION, main_frame=True, refusal=refusal)]
            )

    async def preflight(
        self, workflow: WorkflowVersion, inputs: Mapping[InputName, str], *, timeout_ms: int
    ) -> None:
        """Check every navigate step's URL, raising EgressBlocked for the first the policy refuses.

        A name that does not resolve yet is not a refusal: its navigation decides, with retries.
        """
        for step in workflow.steps:
            if not isinstance(step, NavigateStep):
                continue
            value = step.value
            url = value.value if isinstance(value, LiteralValue) else inputs[value.name]
            try:
                await self.check(url, timeout_ms=timeout_ms)
            except NavigationError:
                continue

    async def _resolve(self, host: str, timeout_ms: int) -> Sequence[IPAddress]:
        found = await self._resolver.resolve(host, timeout_ms=timeout_ms)
        try:
            return tuple(parse_address(text) for text in found)
        except ValueError as error:
            raise NavigationError(
                "the host name resolved to something that is not an address",
                reason=_NAME_NOT_RESOLVED,
                host=host,
            ) from error
