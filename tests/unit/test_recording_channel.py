"""The recorder's channel and messages: strict validation, document order, and the handshake.

No browser: the channel's binding callback is called the way Playwright calls it, with a
source whose frame is either the page's main frame or a child frame.
"""

import asyncio
from typing import cast

import pytest
from playwright.async_api import Page

from mendwork.adapters.browser_playwright.facts import FactsReply
from mendwork.adapters.browser_playwright.recording.channel import LoggingInbound, RecorderChannel
from mendwork.adapters.browser_playwright.recording.messages import (
    PAGE_MESSAGE,
    FieldTextReply,
    ScopeReply,
    to_event,
)
from mendwork.engine.domain.recording import IgnoredReason
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.ports.recording_types import (
    CaptureRef,
    ClickCapture,
    FieldValue,
    FillCapture,
    IgnoredCapture,
    MaskedField,
    PageEvent,
    PageRestored,
    PressCapture,
    PressKey,
    ProtocolViolation,
    SelectCapture,
    SessionClosed,
)
from tests.fakes.recording import NeverStop, Stopped

pytestmark = pytest.mark.asyncio

DOC = "c" * 32


class Frame:
    """Stands in for a Playwright frame; only its identity matters."""


class PageStub:
    def __init__(self) -> None:
        self.main_frame = Frame()


class Transcript:
    """Keeps every payload the channel observed."""

    def __init__(self) -> None:
        self.payloads: list[tuple[str, object]] = []

    def received(self, source: str, payload: object) -> None:
        self.payloads.append((source, payload))


def message(sequence: int, kind: str = "fill", **fields: object) -> dict[str, object]:
    return {"type": kind, "document": DOC, "sequence": sequence, "element": 1, **fields}


def channel() -> tuple[RecorderChannel, PageStub, Transcript]:
    transcript = Transcript()
    recorder = RecorderChannel(transcript)
    page = PageStub()
    recorder.attach(cast(Page, page))
    return recorder, page, transcript


def main(page: PageStub) -> dict[str, object]:
    return {"frame": page.main_frame}


async def test_messages_are_released_in_their_document_order() -> None:
    recorder, page, transcript = channel()

    await recorder._on_binding(main(page), message(2, "select"))
    assert recorder.drain() == ()
    await recorder._on_binding(main(page), message(1))
    await recorder._on_binding(main(page), message(1))

    ref = CaptureRef(document=DOC, sequence=1, element=1)
    assert recorder.drain() == (
        FillCapture(ref=ref),
        SelectCapture(ref=ref.model_copy(update={"sequence": 2})),
    )
    assert [source for source, _ in transcript.payloads] == ["binding"] * 3


async def test_a_message_carrying_a_value_is_rejected_and_ends_the_recording() -> None:
    recorder, page, transcript = channel()
    payload = message(1, value="hunter2")

    await recorder._on_binding(main(page), payload)

    assert recorder.drain() == (ProtocolViolation(),)
    assert transcript.payloads == [("binding", payload)]


async def test_a_child_frame_may_only_report_ignored_interactions() -> None:
    recorder, _, _ = channel()
    child = {"frame": Frame()}

    ignored = {"type": "ignored", "document": DOC, "sequence": 2, "reason": "frame"}

    await recorder._on_binding(child, message(1, "click"))
    await recorder._on_binding(child, ignored)

    assert recorder.drain() == (IgnoredCapture(reason=IgnoredReason.FRAME),)


async def test_a_gesture_holds_the_page_until_it_is_finished() -> None:
    recorder, page, _ = channel()
    call = asyncio.ensure_future(recorder._on_binding(main(page), message(1, "click")))

    event = await recorder.next_event(NeverStop())
    assert event == ClickCapture(ref=CaptureRef(document=DOC, sequence=1, element=1))
    assert not call.done()

    recorder.finish(CaptureRef(document=DOC, sequence=1, element=1))
    await call
    recorder.finish(CaptureRef(document=DOC, sequence=1, element=1))


async def test_closing_releases_every_held_gesture_once() -> None:
    recorder, page, _ = channel()
    call = asyncio.ensure_future(
        recorder._on_binding(main(page), message(1, "press", element=None, key="Enter"))
    )
    assert isinstance(await recorder.next_event(NeverStop()), PressCapture)

    recorder.close()
    recorder.close()
    await call

    assert recorder.drain() == (SessionClosed(),)
    recorder.put(PageRestored())
    assert recorder.drain() == ()


async def test_stopping_returns_none_once_nothing_is_queued() -> None:
    recorder, page, _ = channel()
    await recorder._on_binding(main(page), message(1))

    assert isinstance(await recorder.next_event(Stopped()), FillCapture)
    assert await recorder.next_event(Stopped()) is None


async def test_waiting_for_a_stop_request_ends_the_wait_for_events() -> None:
    recorder, _, _ = channel()
    event = asyncio.Event()

    class Later:
        @property
        def requested(self) -> bool:
            return False

        async def wait(self) -> None:
            await event.wait()

    waiting = asyncio.ensure_future(recorder.next_event(Later()))
    await asyncio.sleep(0)
    event.set()

    assert await waiting is None


async def test_waiting_for_delivery_reports_whether_the_messages_arrived() -> None:
    recorder, page, _ = channel()
    await recorder._on_binding(main(page), message(1))

    assert await recorder.wait_delivered(DOC, 1, timeout_ms=100)
    assert not await recorder.wait_delivered(DOC, 2, timeout_ms=1)


@pytest.mark.parametrize(
    ("payload", "event"),
    [
        (message(1, "click"), ClickCapture(ref=CaptureRef(document=DOC, sequence=1, element=1))),
        (
            message(1, "press", element=None, key="Space"),
            PressCapture(ref=CaptureRef(document=DOC, sequence=1), key=PressKey.SPACE),
        ),
        (
            {"type": "ignored", "document": DOC, "sequence": 1, "reason": "busy"},
            IgnoredCapture(reason=IgnoredReason.BUSY),
        ),
        ({"type": "restored", "document": DOC, "sequence": 1}, PageRestored()),
    ],
)
async def test_every_message_type_becomes_its_page_event(
    payload: dict[str, object], event: PageEvent
) -> None:
    assert to_event(PAGE_MESSAGE.validate_python(payload)) == event


async def test_replies_become_engine_facts_without_values() -> None:
    facts = FactsReply.model_validate(
        {
            "tag": "input",
            "id": "email",
            "name": "email",
            "type": "email",
            "autocomplete": "username",
            "placeholder": None,
            "ariaLabel": None,
            "testId": "login-email",
            "href": None,
            "labelText": "Email address",
            "text": None,
            "ownText": None,
            "nearbyText": ["Sign in"],
            "structuralPath": "main > form > input",
            "box": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.05},
            "textEntry": True,
            "masked": False,
            "inForm": True,
            "formSubmit": False,
            "formHasPassword": True,
        }
    ).facts()

    assert (facts.data_testid, facts.nearby_text, facts.box is not None) == (
        "login-email",
        ("Sign in",),
        True,
    )
    assert FieldTextReply(masked=True, value=None).field_text() == MaskedField()
    assert FieldTextReply(masked=False, value=None).field_text() == FieldValue(value="")
    scope = ScopeReply.model_validate(
        {"tag": "tr", "id": None, "testId": None, "rowHeader": "PO-1"}
    )
    assert scope.ancestor(None).name == ""
    assert scope.ancestor(ElementIdentity(tag="tr", role="row", name="PO-1 x")).role == "row"


async def test_the_logging_observer_logs_sizes_not_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    LoggingInbound().received("field_text", {"masked": False, "value": "hunter2"})

    assert "hunter2" not in capsys.readouterr().err
