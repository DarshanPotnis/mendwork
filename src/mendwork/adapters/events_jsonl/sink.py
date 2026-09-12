"""The EventSink port as JSON lines on a text stream: one event per line, flushed at once."""

from typing import TextIO

from mendwork.engine.domain.events import RunEvent


class JsonLinesEventSink:
    """Writes each event as one compact JSON object followed by a newline.

    Flushing per event lets a consumer reading a pipe act on a step as it happens.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    async def emit(self, event: RunEvent) -> None:
        self._stream.write(event.model_dump_json() + "\n")
        self._stream.flush()
