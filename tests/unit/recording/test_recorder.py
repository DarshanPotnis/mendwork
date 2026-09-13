"""The recorder end to end on a scripted page: steps, ignorable interactions, and fatal ones."""

from collections.abc import Callable

import pytest

from mendwork.engine.domain.enums import ActionType, CheckpointKind, RiskLevel
from mendwork.engine.domain.recording import (
    IgnoredReason,
    InputHint,
    InteractionIgnored,
    LiteralDraft,
    NavigationIgnored,
    Recording,
    RecordingNotice,
    SecretDraft,
    StepRecorded,
)
from mendwork.engine.errors import NavigationError, RecordingUnusable, TargetNotActionable
from mendwork.engine.ports.recording_types import (
    ClickCapture,
    FieldValue,
    FillCapture,
    IgnoredCapture,
    MaskedField,
    NavigationCommitted,
    NavigationInitiator,
    NavigationRecord,
    PageEvent,
    PageRestored,
    PressCapture,
    PressKey,
    ProtocolViolation,
    SelectCapture,
    SessionClosed,
)
from mendwork.engine.recording.recorder import Recorder
from tests.fakes.browser import FakeBrowser, FakeElement, download
from tests.fakes.ports import SequenceRandom
from tests.fakes.recording import FakeRecordingBrowser, FakeRecordingLauncher, NeverStop, Stopped
from tests.unit.recording.builders import (
    CONFIG,
    DOC,
    START_URL,
    add_button,
    add_heading,
    browser,
    by_test_id,
    facts,
    ref,
    role,
    selector,
)

pytestmark = pytest.mark.asyncio

DASHBOARD = "https://portal.example.test/dashboard.html"


class Notices:
    """Keeps every notice the recorder sends."""

    def __init__(self) -> None:
        self.notices: list[RecordingNotice] = []

    async def notify(self, notice: RecordingNotice) -> None:
        self.notices.append(notice)


def start_page() -> FakeRecordingBrowser:
    page = browser(title="Sign in · Portal")
    add_heading(page, "Sign in")
    return page


def add_field(
    page: FakeRecordingBrowser,
    key: str,
    *,
    element: int,
    label: str,
    value: str,
    input_type: str = "text",
    masked: bool = False,
) -> None:
    page.elements[key] = FakeElement(
        tag="input", role="textbox", name=label, input_type=input_type, value=value
    )
    page.facts[key] = facts(
        "input",
        data_testid=key,
        type=input_type,
        label_text=label,
        text_entry=True,
        masked=masked,
        in_form=True,
        form_has_password=True,
    )
    page.captured[(DOC, element)] = key
    page.finds[by_test_id(key)] = key
    page.finds[role("textbox", label)] = key
    page.finds[selector(strategy="label", value=label)] = key
    page.field_texts[key] = FieldValue(value=value)


def navigates_to(
    url: str, new_heading: str, initiator: NavigationInitiator = NavigationInitiator.PAGE
) -> Callable[[FakeBrowser], None]:
    def effect(fake: FakeBrowser) -> None:
        assert isinstance(fake, FakeRecordingBrowser)
        fake.commit_navigation(url, initiator)
        fake.landmarks = ()
        add_heading(fake, new_heading)

    return effect


def recorder(page: FakeRecordingBrowser, notices: Notices) -> Recorder:
    return Recorder(
        launcher=FakeRecordingLauncher(page),
        observer=notices,
        timer=page.timer,
        randomness=SequenceRandom(),
        config=CONFIG,
    )


async def record(page: FakeRecordingBrowser, *events: PageEvent) -> tuple[Recording, Notices]:
    page.events.extend(events)
    notices = Notices()
    return await recorder(page, notices).record(START_URL, NeverStop()), notices


async def fatal(page: FakeRecordingBrowser, *events: PageEvent) -> str:
    with pytest.raises(RecordingUnusable) as caught:
        await record(page, *events)
    return str(caught.value.context["reason"])


def actions(recording: Recording) -> list[ActionType]:
    return [step.action for step in recording.steps]


def commit(sequence: int, url: str, initiator: NavigationInitiator) -> NavigationCommitted:
    return NavigationCommitted(
        record=NavigationRecord(sequence=sequence, url=url, initiator=initiator)
    )


async def test_a_sign_in_is_recorded_without_reading_the_password() -> None:
    page = start_page()
    add_field(
        page,
        "email",
        element=1,
        label="Email address",
        value="ada@example.test",
        input_type="email",
    )
    add_field(
        page,
        "password",
        element=2,
        label="Password",
        value="hunter2",
        input_type="password",
        masked=True,
    )
    button = add_button(
        page,
        "sign-in",
        name="Sign in",
        element=3,
        form_submit=True,
        in_form=True,
        form_has_password=True,
    )
    button.on_action = navigates_to(DASHBOARD, "Welcome back")

    recording, notices = await record(
        page,
        FillCapture(ref=ref(1, 1)),
        FillCapture(ref=ref(2, 2)),
        ClickCapture(ref=ref(3, 3)),
    )

    assert actions(recording) == [
        ActionType.NAVIGATE,
        ActionType.FILL,
        ActionType.FILL,
        ActionType.CLICK,
    ]
    start, email, password, sign_in = recording.steps
    assert start.value == LiteralDraft(value=START_URL, hint=InputHint.START_URL)
    assert [checkpoint.kind for checkpoint in start.checkpoints] == [CheckpointKind.ELEMENT_VISIBLE]
    assert email.value == LiteralDraft(value="ada@example.test", hint=InputHint.EMAIL)
    assert isinstance(password.value, SecretDraft)
    assert page.field_reads == ["email"]
    assert [checkpoint.kind for checkpoint in password.checkpoints] == [
        CheckpointKind.FIELD_HAS_VALUE
    ]
    assert sign_in.description == "CLICK the 'Sign in' button"
    assert sign_in.risk is RiskLevel.CAUTION
    assert [checkpoint.kind for checkpoint in sign_in.checkpoints] == [
        CheckpointKind.URL_MATCHES,
        CheckpointKind.ELEMENT_VISIBLE,
        CheckpointKind.NO_ERROR_BANNER,
    ]
    assert page.armed == [ref(3, 3)]
    assert page.disarmed == [ref(3, 3)]
    assert page.finished == [ref(3, 3)]
    assert [notice.kind for notice in notices.notices] == ["step_recorded"] * 4


async def test_the_navigation_a_click_caused_is_not_recorded_twice() -> None:
    page = start_page()
    add_button(page, "open", name="View reports").on_action = navigates_to(DASHBOARD, "Reports")

    recording, notices = await record(
        page,
        ClickCapture(ref=ref(1, 1)),
        commit(1, DASHBOARD, NavigationInitiator.PAGE),
    )

    assert actions(recording) == [ActionType.NAVIGATE, ActionType.CLICK]
    assert not any(isinstance(notice, NavigationIgnored) for notice in notices.notices)


async def test_a_ctrl_click_is_ignored_and_the_plain_click_after_it_is_one_step() -> None:
    page = start_page()
    add_button(page)

    recording, notices = await record(
        page, IgnoredCapture(reason=IgnoredReason.MODIFIED_CLICK), ClickCapture(ref=ref(2, 1))
    )

    assert actions(recording) == [ActionType.NAVIGATE, ActionType.CLICK]
    assert recording.ignored_count == 1
    assert InteractionIgnored(reason=IgnoredReason.MODIFIED_CLICK) in notices.notices


async def test_an_interaction_during_a_step_names_that_step() -> None:
    page = start_page()
    add_button(page)

    recording, _ = await record(
        page, ClickCapture(ref=ref(1, 1)), IgnoredCapture(reason=IgnoredReason.BUSY)
    )

    assert recording.notices[-1] == InteractionIgnored(reason=IgnoredReason.BUSY, during_step=1)


async def test_a_click_on_a_disabled_or_covered_control_is_ignored() -> None:
    page = start_page()
    add_button(page, "disabled", name="Disabled", element=1).enabled = False
    add_button(page, "covered", name="Covered", element=2).action_error = TargetNotActionable(
        "covered", reason="timeout"
    )
    add_button(page, "save", name="Save", element=3)

    recording, _ = await record(
        page,
        ClickCapture(ref=ref(1, 1)),
        ClickCapture(ref=ref(2, 2)),
        ClickCapture(ref=ref(3, 3)),
    )

    assert actions(recording) == [ActionType.NAVIGATE, ActionType.CLICK]
    ignored = [
        notice.reason for notice in recording.notices if isinstance(notice, InteractionIgnored)
    ]
    assert ignored == [IgnoredReason.NOT_ACTIONABLE, IgnoredReason.NOT_ACTIONABLE]
    assert page.finished == [ref(1, 1), ref(2, 2), ref(3, 3)]


async def test_keys_selects_merged_fills_masked_reads_and_downloads() -> None:
    page = start_page()
    add_field(page, "city", element=1, label="City", value="Lisbon")
    add_field(page, "code", element=2, label="Code", value="1234")
    page.field_texts["code"] = MaskedField()
    page.elements["country"] = FakeElement(tag="select", role="combobox", name="Country")
    page.facts["country"] = facts("select", data_testid="country", label_text="Country")
    page.captured[(DOC, 3)] = "country"
    page.finds[by_test_id("country")] = "country"
    page.field_texts["country"] = FieldValue(value="Portugal")
    report = add_button(page, "report", name="Get report", element=4)
    report.on_action = lambda fake: fake.emit_download(download("report.csv"))

    recording, notices = await record(
        page,
        FillCapture(ref=ref(1, 1)),
        FillCapture(ref=ref(2, 1)),
        FillCapture(ref=ref(3, 2)),
        SelectCapture(ref=ref(4, 3)),
        PressCapture(ref=ref(5, 1), key=PressKey.ENTER),
        PressCapture(ref=ref(6, None), key=PressKey.ESCAPE),
        ClickCapture(ref=ref(7, 4)),
    )

    assert actions(recording) == [
        ActionType.NAVIGATE,
        ActionType.FILL,
        ActionType.FILL,
        ActionType.SELECT,
        ActionType.PRESS,
        ActionType.PRESS,
        ActionType.CLICK,
    ]
    _, city, code, country, enter, escape, get_report = recording.steps
    replaced = notices.notices[2]
    assert isinstance(replaced, StepRecorded)
    assert replaced.replaced
    assert (city.index, city.step_id) == (1, "fill_city")
    assert isinstance(code.value, SecretDraft)
    assert code.value.reason == "it was masked when its value was read"
    assert country.value == LiteralDraft(value="Portugal")
    assert enter.key == "Enter"
    assert enter.description == "PRESS Enter on the 'City' field"
    assert [checkpoint.kind for checkpoint in enter.checkpoints] == [CheckpointKind.NO_ERROR_BANNER]
    assert escape.target is None
    assert escape.description == "PRESS Escape"
    assert [checkpoint.kind for checkpoint in get_report.checkpoints] == [
        CheckpointKind.DOWNLOAD_COMPLETED
    ]
    assert get_report.risk is RiskLevel.SAFE


async def test_navigations_outside_steps_become_steps_or_notices() -> None:
    page = start_page()
    add_button(page)
    orders = "https://portal.example.test/orders.html"
    login = "https://portal.example.test/login.html"

    recording, _ = await record(
        page,
        commit(1, orders, NavigationInitiator.BROWSER),
        commit(1, orders, NavigationInitiator.BROWSER),
        commit(2, login, NavigationInitiator.PAGE),
        commit(3, "about:blank", NavigationInitiator.BROWSER),
        ClickCapture(ref=ref(1, 1)),
    )

    assert actions(recording) == [ActionType.NAVIGATE, ActionType.NAVIGATE, ActionType.CLICK]
    assert recording.steps[1].value == LiteralDraft(value=orders)
    ignored = [notice.url for notice in recording.notices if isinstance(notice, NavigationIgnored)]
    assert ignored == [login, "about:blank"]


async def test_stopping_commits_edited_fields_before_it_ends() -> None:
    page = start_page()
    add_field(page, "city", element=1, label="City", value="Lisbon")
    page.pending.extend([FillCapture(ref=ref(1, 1)), SessionClosed(), FillCapture(ref=ref(2, 1))])

    recording = await recorder(page, Notices()).record(START_URL, Stopped())

    assert actions(recording) == [ActionType.NAVIGATE, ActionType.FILL]
    assert page.flushes == 1


async def test_closing_the_window_ends_the_recording_with_what_was_captured() -> None:
    page = start_page()
    add_button(page)

    recording, _ = await record(
        page, ClickCapture(ref=ref(1, 1)), SessionClosed(), ClickCapture(ref=ref(2, 1))
    )

    assert actions(recording) == [ActionType.NAVIGATE, ActionType.CLICK]
    assert page.flushes == 0


async def test_a_page_with_nothing_recorded_after_the_start_is_unusable() -> None:
    assert await fatal(start_page()) == "nothing_recorded"


async def test_an_unreachable_start_url_is_unusable() -> None:
    page = start_page()
    page.navigations.append(NavigationError("refused", reason="ERR_CONNECTION_REFUSED"))

    assert await fatal(page) == "start_url_unreachable"


async def test_a_restored_page_and_an_unknown_message_are_unusable() -> None:
    assert await fatal(start_page(), PageRestored()) == "page_restored"
    assert await fatal(start_page(), ProtocolViolation()) == "protocol_violation"


async def test_an_element_gone_before_its_step_is_unusable() -> None:
    assert await fatal(start_page(), ClickCapture(ref=ref(1, 9))) == "element_gone"
    assert await fatal(start_page(), FillCapture(ref=ref(1, 9))) == "element_gone"
    page = start_page()
    add_button(page)
    page.arm_succeeds = False
    assert await fatal(page, ClickCapture(ref=ref(1, 1))) == "element_gone"
    assert page.finished == [ref(1, 1)]


async def test_a_new_tab_or_a_browser_navigation_during_a_step_is_unusable() -> None:
    page = start_page()
    add_button(page).on_action = lambda fake: setattr(fake, "opened_pages", 1)
    assert await fatal(page, ClickCapture(ref=ref(1, 1))) == "new_page_opened"

    page = start_page()
    add_button(page).on_action = navigates_to(DASHBOARD, "Elsewhere", NavigationInitiator.BROWSER)
    assert await fatal(page, ClickCapture(ref=ref(1, 1))) == "browser_navigation_during_step"


async def test_a_list_that_looks_like_a_credential_field_is_unusable() -> None:
    page = start_page()
    page.elements["pin"] = FakeElement(tag="select", role="combobox", name="PIN")
    page.facts["pin"] = facts("select", data_testid="pin", label_text="PIN")
    page.captured[(DOC, 1)] = "pin"
    page.finds[by_test_id("pin")] = "pin"

    assert await fatal(page, SelectCapture(ref=ref(1, 1))) == "unrecordable_target"
    assert page.field_reads == []


async def test_a_value_too_long_to_store_is_unusable() -> None:
    page = start_page()
    add_field(page, "notes", element=1, label="Notes", value="x" * 5000)

    assert await fatal(page, FillCapture(ref=ref(1, 1))) == "unrecordable_target"


async def test_a_launcher_that_cannot_open_raises_its_error() -> None:
    page = start_page()
    launcher = FakeRecordingLauncher(page, error=NavigationError("no browser", reason="x"))
    failing = Recorder(
        launcher=launcher,
        observer=Notices(),
        timer=page.timer,
        randomness=SequenceRandom(),
        config=CONFIG,
    )

    with pytest.raises(NavigationError):
        await failing.record(START_URL, NeverStop())
