"""In-memory ArtifactStore, RunRecords, EventSink, SecretResolver, RandomSource, and RunIds."""

from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import SecretStr, TypeAdapter

from mendwork.engine.domain.events import RunEvent
from mendwork.engine.domain.identifiers import SecretName
from mendwork.engine.domain.runs import ArtifactName, Run, RunId, parse_run_id
from mendwork.engine.errors import (
    ArtifactStoreUnavailable,
    RunBusy,
    SecretUnavailable,
    UnknownRun,
)

RUN_EVENT: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)


class InMemoryArtifactStore:
    """Keeps written bytes and adopted file paths per run, and every write in order."""

    def __init__(self, *, fail: bool = False) -> None:
        self.files: dict[tuple[RunId, ArtifactName], bytes] = {}
        self.adopted: dict[tuple[RunId, ArtifactName], Path] = {}
        self.writes: list[tuple[RunId, ArtifactName, bytes]] = []
        self.fail = fail

    async def write(self, run_id: RunId, name: ArtifactName, data: bytes) -> ArtifactName:
        if self.fail:
            raise ArtifactStoreUnavailable("the store is failing on purpose", name=name)
        self.files[(run_id, name)] = data
        self.writes.append((run_id, name, data))
        return name

    async def adopt(self, run_id: RunId, name: ArtifactName, source: Path) -> ArtifactName:
        if self.fail:
            raise ArtifactStoreUnavailable("the store is failing on purpose", name=name)
        self.adopted[(run_id, name)] = source
        return name

    def names(self, run_id: RunId) -> list[str]:
        return sorted(name for run, name in [*self.files, *self.adopted] if run == run_id)

    def run_record(self, run_id: RunId) -> Run:
        return Run.model_validate_json(self.files[(run_id, ArtifactName("run.json"))])

    def record_writes(self, run_id: RunId) -> list[Run]:
        """Every version of the run's record, in the order it was written."""
        return [
            Run.model_validate_json(data)
            for run, name, data in self.writes
            if run == run_id and name == "run.json"
        ]


class InMemoryRunRecords:
    """Claims held in memory, and records read back from an in-memory store."""

    def __init__(self, store: InMemoryArtifactStore | None = None) -> None:
        self.store = store or InMemoryArtifactStore()
        self.held: set[RunId] = set()
        self.claimed: list[RunId] = []

    @asynccontextmanager
    async def claim(self, run_id: RunId) -> AsyncIterator[None]:
        if run_id in self.held:
            raise RunBusy(f"run {run_id} is being run or resumed by another process")
        self.held.add(run_id)
        self.claimed.append(run_id)
        try:
            yield
        finally:
            self.held.discard(run_id)

    async def is_claimed(self, run_id: RunId) -> bool:
        return run_id in self.held

    async def load_run(self, run_id: RunId) -> Run:
        data = self.store.files.get((run_id, ArtifactName("run.json")))
        if data is None:
            raise UnknownRun(f"no run {run_id}", run_id=run_id)
        return Run.model_validate_json(data)

    async def load_workflow(self, run_id: RunId) -> bytes:
        data = self.store.files.get((run_id, ArtifactName("workflow.json")))
        if data is None:
            raise UnknownRun(f"no workflow snapshot for run {run_id}", run_id=run_id)
        return data


class RecordingEventSink:
    """Keeps every event, after proving it survives a JSON round trip."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def emit(self, event: RunEvent) -> None:
        assert RUN_EVENT.validate_json(event.model_dump_json()) == event
        self.events.append(event)

    @property
    def types(self) -> list[str]:
        return [event.type for event in self.events]


class DictSecretResolver:
    """Secrets from a dictionary; empty values count as missing, like the real resolver."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        self.resolved: list[str] = []

    async def missing(self, names: Sequence[SecretName]) -> tuple[SecretName, ...]:
        return tuple(name for name in names if not self._values.get(name))

    async def resolve(self, name: SecretName) -> SecretStr:
        value = self._values.get(name)
        if not value:
            raise SecretUnavailable("secret is not available", name=name)
        self.resolved.append(name)
        return SecretStr(value)


class SequenceRandom:
    """Returns the given numbers in order, repeating the last one."""

    def __init__(self, values: Iterable[float] = (0.0,)) -> None:
        self._values = list(values)

    def unit(self) -> float:
        return self._values.pop(0) if len(self._values) > 1 else self._values[0]


class SequentialRunIds:
    """Run ids 20260911T000000Z-00000001, -00000002, …"""

    def __init__(self) -> None:
        self._count = 0

    def new_run_id(self) -> RunId:
        self._count += 1
        return parse_run_id(f"20260911T000000Z-{self._count:08x}")
