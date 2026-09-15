"""One session's egress decisions: what was refused, and which connections failed upstream.

The gateway and the document filter write here as the browser works; the session reads it after a
navigation or an action. Through the gateway, every failed connection looks the same to Chromium
(``ERR_SOCKS_CONNECTION_FAILED``), so the log is what tells a refusal from a server that did not
answer, and a refused connect from a name that did not resolve.
"""

from dataclasses import dataclass, field

from mendwork.engine.safety.egress_blocks import EgressBlock


@dataclass(frozen=True, slots=True)
class UpstreamFailure:
    """A connection the policy allowed that still could not be made."""

    host: str
    port: int
    reason: str
    """Chromium's own code for the same failure on a direct connection, such as
    ``ERR_CONNECTION_REFUSED``, so retries classify it the same way."""


@dataclass
class EgressLog:
    """Refusals waiting to be reported, and upstream failures in the order they happened."""

    _blocks: list[EgressBlock] = field(default_factory=list)
    _failures: list[tuple[int, UpstreamFailure]] = field(default_factory=list)
    _sequence: int = 0

    def mark(self) -> int:
        """A point in the log; failures recorded later are ``since`` it."""
        return self._sequence

    def block(self, block: EgressBlock) -> None:
        """Record a refusal."""
        self._sequence += 1
        self._blocks.append(block)

    def upstream_failure(self, failure: UpstreamFailure) -> None:
        """Record a connection that failed after the policy allowed it."""
        self._sequence += 1
        self._failures.append((self._sequence, failure))

    def take_blocks(self) -> tuple[EgressBlock, ...]:
        """Every refusal not yet reported; each is reported once."""
        taken, self._blocks = tuple(self._blocks), []
        return taken

    def failure_since(self, mark: int) -> UpstreamFailure | None:
        """The latest upstream failure recorded after ``mark``, if any."""
        later = [failure for sequence, failure in self._failures if sequence > mark]
        return later[-1] if later else None
