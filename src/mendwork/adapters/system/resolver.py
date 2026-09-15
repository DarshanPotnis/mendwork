"""Host name resolution through the operating system's resolver, bounded by a timeout."""

import asyncio
import socket
from typing import Final

from mendwork.engine.errors import NavigationError

NAME_NOT_RESOLVED: Final = "ERR_NAME_RESOLUTION_FAILED"
"""The reason a failed lookup carries: Chromium's own code, so retries classify it the same way."""


class SystemHostResolver:
    """A HostResolver on ``getaddrinfo``, run off the event loop."""

    async def resolve(self, host: str, *, timeout_ms: int) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                found = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as error:
            # TimeoutError is an OSError, so a lookup that outlives its timeout lands here too.
            raise NavigationError(
                "the host name could not be resolved",
                reason=NAME_NOT_RESOLVED,
                host=host,
                error_type=type(error).__name__,
            ) from error
        addresses = tuple(dict.fromkeys(str(info[4][0]).split("%", 1)[0] for info in found))
        if not addresses:
            raise NavigationError(
                "the host name resolved to no address", reason=NAME_NOT_RESOLVED, host=host
            )
        return addresses
