"""The recorder's channel: where page messages enter Python, in order, one document at a time.

Every payload the recording page scripts send or return passes through ``observe`` before
it is parsed, so an ``InboundObserver`` sees everything that reached Python from the page.
In production the observer logs each payload's kind and size, never its content; a test
keeps the payloads, which is how it proves a secret never arrived.

Messages are released to the recorder in each document's sequence order. A click or key
message is answered only when the recorder finishes it, which is what keeps the page
holding input back while the step is recorded.
"""

import asyncio
import json
from collections.abc import Mapping
from contextlib import suppress
from typing import Final, Protocol

import structlog
from playwright.async_api import BrowserContext, Frame, Page
from pydantic import ValidationError

from mendwork.adapters.browser_playwright.recording.messages import (
    PAGE_MESSAGE,
    IgnoredMessage,
    PageMessage,
    is_gesture,
    to_event,
)
from mendwork.engine.ports.recording import StopSignal
from mendwork.engine.ports.recording_types import (
    CaptureRef,
    PageEvent,
    ProtocolViolation,
    SessionClosed,
)

BINDING_NAME: Final = "__mendwork_recorder_binding"


class InboundObserver(Protocol):
    """Sees every payload that reaches Python from a recording page, before it is parsed."""

    def received(self, source: str, payload: object) -> None:
        """One payload, and which script or binding delivered it."""
        ...


class LoggingInbound:
    """Logs each payload's source and size at debug level. The content is never logged."""

    def __init__(self) -> None:
        self._log = structlog.stdlib.get_logger("mendwork.recording.inbound")

    def received(self, source: str, payload: object) -> None:
        size = len(json.dumps(payload, default=str))
        self._log.debug("page_payload", source=source, size=size)


class RecorderChannel:
    """Orders, validates, and queues one recording page's messages."""

    def __init__(self, inbound: InboundObserver) -> None:
        self._inbound = inbound
        self._queue: asyncio.Queue[PageEvent] = asyncio.Queue()
        self._next: dict[str, int] = {}
        self._held: dict[str, dict[int, PageMessage | None]] = {}
        self._waiting: dict[tuple[str, int], asyncio.Future[None]] = {}
        self._delivered = asyncio.Condition()
        self._main_frame: Frame | None = None
        self._closed = False
        self._log = structlog.stdlib.get_logger("mendwork.recording.channel")

    async def install(self, context: BrowserContext, script: str) -> None:
        """Expose the binding and install the recorder in every future document."""
        await context.expose_binding(BINDING_NAME, self._on_binding)
        await context.add_init_script(script=script)

    def attach(self, page: Page) -> None:
        """Accept captures from this page's main frame only."""
        self._main_frame = page.main_frame

    def observe(self, source: str, payload: object) -> None:
        """Report a payload that reached Python from the page."""
        self._inbound.received(source, payload)

    def put(self, event: PageEvent) -> None:
        """Queue an event the adapter itself observed, such as a navigation."""
        if not self._closed:
            self._queue.put_nowait(event)

    async def next_event(self, stop: StopSignal) -> PageEvent | None:
        """The next event, or None once stopping is requested and nothing is queued."""
        if not self._queue.empty():
            return self._queue.get_nowait()
        if stop.requested:
            return None
        getter = asyncio.ensure_future(self._queue.get())
        stopper = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({getter, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (getter, stopper):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
        return getter.result() if getter.done() and not getter.cancelled() else None

    def drain(self) -> tuple[PageEvent, ...]:
        """Every event still queued."""
        events: list[PageEvent] = []
        while not self._queue.empty():
            events.append(self._queue.get_nowait())
        return tuple(events)

    async def wait_delivered(self, document: str, sequence: int, *, timeout_ms: int) -> bool:
        """Whether every message of a document up to ``sequence`` arrived within the timeout."""
        try:
            async with asyncio.timeout(timeout_ms / 1000), self._delivered:
                await self._delivered.wait_for(lambda: self._next.get(document, 1) > sequence)
        except TimeoutError:
            self._log.warning("recorder_flush_incomplete", sequence=sequence)
            return False
        return True

    def finish(self, ref: CaptureRef) -> None:
        """Answer a held-back gesture's message, releasing the page."""
        future = self._waiting.pop((ref.document, ref.sequence), None)
        if future is not None and not future.done():
            future.set_result(None)

    def close(self) -> None:
        """Release every waiting page call and report the session closed, once."""
        if self._closed:
            return
        self._closed = True
        for future in self._waiting.values():
            if not future.done():
                future.set_result(None)
        self._waiting.clear()
        self._queue.put_nowait(SessionClosed())

    async def _on_binding(self, source: Mapping[str, object], payload: object) -> None:
        self.observe("binding", payload)
        try:
            message = PAGE_MESSAGE.validate_python(payload)
        except ValidationError as error:
            self._log.error("recorder_message_rejected", problems=error.error_count())
            self.put(ProtocolViolation())
            return
        if source.get("frame") is not self._main_frame and not isinstance(message, IgnoredMessage):
            # A child frame may only say an interaction was ignored. Its place in the sequence
            # is still taken, so the frame's later messages are not held back behind it.
            self._log.warning("recorder_message_from_child_frame_rejected", type=message.type)
            await self._accept(message, deliver=False)
            return
        future: asyncio.Future[None] | None = None
        if is_gesture(message) and not self._closed:
            future = asyncio.get_running_loop().create_future()
            self._waiting[(message.document, message.sequence)] = future
        await self._accept(message, deliver=True)
        if future is not None:
            await future

    async def _accept(self, message: PageMessage, *, deliver: bool) -> None:
        expected = self._next.get(message.document, 1)
        if message.sequence < expected:
            self._log.warning("recorder_message_repeated", sequence=message.sequence)
            return
        held = self._held.setdefault(message.document, {})
        held[message.sequence] = message if deliver else None
        async with self._delivered:
            while expected in held:
                released = held.pop(expected)
                if released is not None:
                    self.put(to_event(released))
                expected += 1
            self._next[message.document] = expected
            self._delivered.notify_all()
