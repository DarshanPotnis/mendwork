"""The RandomSource port backed by the operating system's random source."""

import random


class SystemRandomSource:
    """Unpredictable numbers, so concurrent workers never share a jitter sequence."""

    def __init__(self) -> None:
        self._random = random.SystemRandom()

    def unit(self) -> float:
        return self._random.random()
