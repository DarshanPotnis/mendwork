"""The RandomSource port: randomness the engine needs, injectable for deterministic tests."""

from typing import Protocol


class RandomSource(Protocol):
    """Uniform random numbers, used for retry jitter."""

    def unit(self) -> float:
        """A number in the half-open interval [0, 1)."""
        ...
