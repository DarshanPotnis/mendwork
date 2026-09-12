"""A Timer whose time moves only when a test or a fake moves it."""


class FakeTimer:
    """Monotonic time under test control; pauses are recorded and advance the time."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start
        self.pauses: list[float] = []

    def monotonic(self) -> float:
        return self._now

    async def pause(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self._now += seconds

    def advance_ms(self, milliseconds: float) -> None:
        self._now += milliseconds / 1000
