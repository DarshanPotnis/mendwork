"""The RunIdGenerator port: a UTC timestamp plus 32 random bits."""

import secrets

from mendwork.engine.domain.runs import RunId, parse_run_id
from mendwork.engine.ports.clock import Clock


class TimestampRunIds:
    """Run ids such as ``20260911T141502Z-7c1e09ab``.

    They sort by start time, which makes a directory of runs readable, and the random
    part keeps two runs started in the same second apart.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def new_run_id(self) -> RunId:
        return parse_run_id(f"{self._clock.now():%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}")
