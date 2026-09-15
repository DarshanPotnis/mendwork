"""Ctrl+C and SIGTERM for commands that execute a run: cancel first, abort on the second.

Real signals are raised in-process, as the recorder's own interrupt test does.
"""

import asyncio
import io
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.apps.cli.interrupts import RunInterrupts, RunWitness, abort_run
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.runs import (
    ArtifactName,
    IrreversibleDispatch,
    Run,
    RunStatus,
    StepResult,
    StepStatus,
    parse_run_id,
)
from mendwork.engine.replay.journal import encode_run
from tests.fakes.clock import FakeClock
from tests.fakes.ports import RecordingEventSink

pytestmark = pytest.mark.asyncio

RUN_ID: Final = parse_run_id("20260914T100000Z-000000aa")
NOW: Final = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
RECORD: Final = ArtifactName("run.json")


async def parked() -> None:
    await asyncio.Event().wait()


async def test_a_first_interrupt_cancels_what_it_watches_and_a_second_aborts() -> None:
    aborted = asyncio.Event()
    interrupts = RunInterrupts(abort=aborted.set)

    async with interrupts.connected():
        task = asyncio.ensure_future(parked())
        interrupts.watch(task.cancel)
        signal.raise_signal(signal.SIGINT)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (interrupts.interrupted, aborted.is_set()) == (True, False)

        signal.raise_signal(signal.SIGINT)
        await aborted.wait()


async def test_sigterm_is_handled_like_a_first_ctrl_c() -> None:
    interrupts = RunInterrupts(abort=lambda: pytest.fail("a single signal never aborts"))

    async with interrupts.connected():
        task = asyncio.ensure_future(parked())
        interrupts.watch(task.cancel)
        signal.raise_signal(signal.SIGTERM)
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_an_interrupt_inside_a_deferred_write_takes_effect_when_the_write_ends() -> None:
    interrupts = RunInterrupts(abort=lambda: pytest.fail("a single signal never aborts"))
    cancelled: list[bool] = []
    inside = False

    async with interrupts.connected():
        interrupts.watch(lambda: cancelled.append(inside))
        async with interrupts.deferred():
            inside = True
            signal.raise_signal(signal.SIGINT)
            await interrupts.arrived()
            assert cancelled == []
            inside = False

    assert cancelled == [False]


async def test_an_interrupt_that_arrived_before_anything_was_watched_cancels_it_at_once() -> None:
    interrupts = RunInterrupts(abort=lambda: pytest.fail("a single signal never aborts"))
    cancelled: list[str] = []

    async with interrupts.connected():
        signal.raise_signal(signal.SIGINT)
        await interrupts.arrived()
        interrupts.watch(lambda: cancelled.append("run"))

    assert cancelled == ["run"]


async def test_the_witness_passes_events_on_and_remembers_their_run() -> None:
    sink = RecordingEventSink()
    witness = RunWitness(sink)
    from mendwork.engine.domain.events import RunFinishedEvent

    event = RunFinishedEvent(
        run_id=RUN_ID, sequence=1, at=NOW, status=RunStatus.SUCCEEDED, duration_ms=0
    )
    await witness.emit(event)

    assert (witness.run_id, sink.events) == (RUN_ID, [event])


def running_record(*, dispatched: bool) -> Run:
    return Run(
        run_id=RUN_ID,
        workflow_id="order_flow",
        workflow_version=1,
        status=RunStatus.RUNNING,
        started_at=NOW,
        steps=(
            StepResult(
                step_id="submit", index=0, action=ActionType.CLICK, status=StepStatus.NOT_RUN
            ),
        ),
        irreversible_dispatched=(
            (IrreversibleDispatch(step_id="submit", index=0, at=NOW, segment=1),)
            if dispatched
            else ()
        ),
    )


@pytest.mark.parametrize(
    ("dispatched", "code", "status"), [(False, 130, "cancelled"), (True, 4, "needs_review")]
)
async def test_an_abort_finishes_the_journaled_record_and_exits_with_its_code(
    tmp_path: Path, dispatched: bool, code: int, status: str
) -> None:
    store = LocalArtifactStore(tmp_path)
    store.write_now(RUN_ID, RECORD, encode_run(running_record(dispatched=dispatched)))
    exits: list[int] = []
    stderr = io.StringIO()

    abort_run(store, RUN_ID, clock=FakeClock(NOW), stderr=stderr, exit_process=exits.append)

    data = store.read_now(RUN_ID, RECORD)
    assert data is not None
    finished = Run.model_validate_json(data)
    assert (exits, finished.status.value) == ([code], status)
    assert finished.error is not None
    assert finished.error.context["interruption"] == "forced"
    assert finished.steps[0].action_outcome_unknown is True
    assert "Aborted by a second interrupt" in stderr.getvalue()


async def test_an_abort_before_the_run_exists_exits_cancelled(tmp_path: Path) -> None:
    exits: list[int] = []

    abort_run(
        LocalArtifactStore(tmp_path),
        None,
        clock=FakeClock(NOW),
        stderr=io.StringIO(),
        exit_process=exits.append,
    )

    assert exits == [130]


async def test_an_abort_that_cannot_read_the_record_says_so_and_exits_3(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    store.write_now(RUN_ID, RECORD, b"{not a record")
    exits: list[int] = []
    stderr = io.StringIO()

    abort_run(store, RUN_ID, clock=FakeClock(NOW), stderr=stderr, exit_process=exits.append)

    assert exits == [3]
    assert "could not be finished (ValidationError)" in stderr.getvalue()
