"""Restoring the last known-good state, and the run memory it relies on."""

from typing import Final

import pytest
import structlog

from mendwork.engine.domain.steps import Step
from mendwork.engine.errors import BrowserUnavailable, HealAbstained
from mendwork.engine.healing.recovery import RestoreRequest, StateRestorer
from mendwork.engine.healing.run_state import RunHealState, StepStart
from mendwork.engine.ports.browser_types import ElementRef, PlainText
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser, FakeElement
from tests.fakes.ports import SequenceRandom
from tests.unit.healing.builders import export_button, step
from tests.unit.replay.builders import browser, config
from tests.workflows import click_step

pytestmark = pytest.mark.asyncio

URL: Final = "https://ledger.example.test/ledger"
CHECKED: Final = [{"kind": "text_present", "text": "Done"}]


def click(step_id: str, risk: str = "safe") -> Step:
    return step(
        click_step(step_id, risk=risk, target=export_button().model_dump(), checkpoints=CHECKED)
    )


def starts(state: RunHealState, *documents: str) -> list[StepStart]:
    recorded = [
        StepStart(index, click(f"step_{index}"), document, f"{URL}?at={index}")
        for index, document in enumerate(documents)
    ]
    for start in recorded:
        state.started(start)
    return recorded


async def test_the_segment_is_every_step_that_began_on_the_same_document() -> None:
    state = RunHealState()
    recorded = starts(state, "d0", "d1", "d1", "d1")

    assert state.segment(3) == tuple(recorded[1:])
    assert state.segment(1) == (recorded[1],)
    assert state.segment(0) == (recorded[0],)


async def test_a_restore_rebases_the_segment_onto_the_reopened_document() -> None:
    state = RunHealState()
    recorded = starts(state, "d0", "d1", "d1")

    state.rebased(recorded[1:], "d9")

    assert [start.document for start in state.segment(2)] == ["d9", "d9"]


async def test_heal_actions_and_verified_heals_are_remembered_per_step() -> None:
    state = RunHealState()
    target = click("export")

    state.count_heal_action(target.id)
    state.count_heal_action(target.id)
    state.remember_verified(target.id, ("signature",))

    assert state.heal_actions(target.id) == 2
    assert state.heal_actions(click("other").id) == 0
    assert state.verified(target.id) == ("signature",)
    assert state.verified(click("other").id) is None


class Replays:
    def __init__(self, error: Exception | None = None) -> None:
        self.replayed: list[str] = []
        self.error = error

    async def __call__(self, start: StepStart, deadline: Deadline) -> None:
        if self.error is not None:
            raise self.error
        self.replayed.append(start.step.id)


def restorer(page: FakeBrowser, state: RunHealState, replays: Replays) -> StateRestorer:
    return StateRestorer(
        browser=page,
        state=state,
        config=config(),
        timer=page.timer,
        randomness=SequenceRandom(),
        scrubber=SecretScrubber(),
        log=structlog.stdlib.get_logger("tests.recovery"),
        replay=replays,
    )


def request(
    page: FakeBrowser, index: int, *, reset: bool = False, typed_into: ElementRef | None = None
) -> RestoreRequest:
    return RestoreRequest(
        index=index,
        step=click(f"step_{index}"),
        attempt=1,
        reset=reset,
        typed_into=typed_into,
        deadline=Deadline.after(page.timer, 5_000),
    )


async def test_a_restore_reopens_the_first_page_of_the_segment_and_replays_the_steps_before() -> (
    None
):
    page = browser()
    page.url = f"{URL}?at=1"
    state = RunHealState()
    starts(state, "d0", "d1", "d1", "d1")
    replays = Replays()

    report = await restorer(page, state, replays).restore(request(page, 3))

    assert (report.restored, report.url, report.replayed) == (
        True,
        f"{URL}?at=1",
        ("step_1", "step_2"),
    )
    assert page.calls_named("navigate") == [f"navigate:{URL}?at=1"]
    assert replays.replayed == ["step_1", "step_2"]
    assert state.segment(3)[0].document == f"doc-{page.document}"


async def test_a_restore_that_would_replay_an_irreversible_step_never_starts() -> None:
    page = browser()
    state = RunHealState()
    state.started(StepStart(0, click("send", risk="irreversible"), "d1", URL))
    state.started(StepStart(1, click("export"), "d1", URL))

    report = await restorer(page, state, Replays()).restore(request(page, 1))

    assert not report.restored
    assert report.reason == "restoring would repeat step send, which is irreversible"
    assert page.calls_named("navigate") == []


async def test_a_failed_replay_means_the_state_was_not_restored() -> None:
    page = browser()
    page.url = f"{URL}?at=0"
    state = RunHealState()
    starts(state, "d1", "d1")
    replays = Replays(
        HealAbstained("step_0 could not be replayed", reason="verified_heal_not_found")
    )

    report = await restorer(page, state, replays).restore(request(page, 1))

    assert not report.restored
    assert report.reason == "HealAbstained: step_0 could not be replayed"


async def test_a_broken_browser_during_a_restore_is_not_swallowed() -> None:
    page = browser()
    page.url = f"{URL}?at=0"
    state = RunHealState()
    starts(state, "d1", "d1")

    with pytest.raises(BrowserUnavailable):
        await restorer(page, state, Replays(BrowserUnavailable("closed"))).restore(request(page, 1))


async def test_a_caution_restore_clears_the_field_the_failed_fill_typed_into() -> None:
    page = browser()
    page.url = f"{URL}?at=0"
    page.elements["field"] = FakeElement(
        tag="input", role="textbox", name="Reference", value="REF-1"
    )
    field = page.pin("field")
    state = RunHealState()
    starts(state, "d1")

    report = await restorer(page, state, Replays()).restore(
        request(page, 0, reset=True, typed_into=field)
    )

    assert report.cleared_field
    assert page.elements["field"].value == ""
    assert page.typed == [PlainText(value="")]


async def test_a_field_that_is_gone_or_read_only_is_not_cleared() -> None:
    page = browser()
    page.url = f"{URL}?at=0"
    page.elements["gone"] = FakeElement(tag="input", name="Reference", attached=False)
    page.elements["locked"] = FakeElement(tag="input", name="Reference", editable=False)
    state = RunHealState()
    starts(state, "d1")
    restoring = restorer(page, state, Replays())

    gone = await restoring.restore(request(page, 0, reset=True, typed_into=page.pin("gone")))
    locked = await restoring.restore(request(page, 0, reset=True, typed_into=page.pin("locked")))
    nothing = await restoring.restore(request(page, 0, reset=True))

    assert [gone.cleared_field, locked.cleared_field, nothing.cleared_field] == [
        False,
        False,
        False,
    ]
    assert page.typed == []
