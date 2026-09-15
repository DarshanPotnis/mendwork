"""The HostResolver port: the addresses a host name resolves to, for the egress policy."""

from typing import Protocol


class HostResolver(Protocol):
    """Looks a host name up, so the addresses checked are the addresses connected to.

    The browser adapter's egress gateway resolves each name once per connection and connects
    only to what it checked, which leaves no gap for a name to resolve differently in between.
    """

    async def resolve(self, host: str, *, timeout_ms: int) -> tuple[str, ...]:
        """Every address the name resolves to, as text, without IPv6 zones or duplicates.

        Raises NavigationError with ``reason`` ``ERR_NAME_RESOLUTION_FAILED`` when the name does
        not resolve within the timeout.
        """
        ...
