"""The ArtifactStore port: where a run's evidence is kept."""

from pathlib import Path
from typing import Protocol

from mendwork.engine.domain.runs import ArtifactName, RunId


class ArtifactStore(Protocol):
    """Stores files under one run. Names are validated relative paths, never absolute.

    Raises ArtifactStoreUnavailable when a write fails, because evidence that silently
    went missing would be mistaken for evidence that never existed.
    """

    async def write(self, run_id: RunId, name: ArtifactName, data: bytes) -> ArtifactName:
        """Store bytes atomically, replacing an earlier file of the same name."""
        ...

    async def adopt(self, run_id: RunId, name: ArtifactName, source: Path) -> ArtifactName:
        """Move a file the browser produced, such as a download or trace, into the run."""
        ...
