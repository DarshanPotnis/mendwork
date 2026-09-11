"""A WorkflowStore on the local file system: one directory per workflow, one file per version.

Layout: ``<root>/<workflow_id>/v0001.yaml``, ``v0002.yaml``, and so on. The latest version
is derived from the files; there is no pointer file that could disagree with them.

Publishing never overwrites. The version is written to a temporary file in the same
directory and synced, then given its final name with link(2), which fails if the name
exists. Two writers racing for the same number therefore produce exactly one file and
one VersionConflict, and a reader only ever sees complete files.
"""

import asyncio
import re
from pathlib import Path
from typing import Final

import structlog

from mendwork.adapters.storage_fs.file_ops import FileOps, OsFileOps
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.identifiers import WorkflowId, parse_workflow_id
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import (
    PolicyViolation,
    ValidationIssue,
    VersionConflict,
    WorkflowValidationError,
)

_VERSION_FILE: Final = re.compile(r"v([0-9]+)\.yaml")


def version_file_name(version: int) -> str:
    """The one canonical file name for a version number."""
    return f"v{version:04d}.yaml"


class FileWorkflowStore:
    """Append-only workflow versions under a root directory, bound to one tenant's root."""

    def __init__(
        self, root: Path, codec: WorkflowYamlCodec, *, file_ops: FileOps | None = None
    ) -> None:
        self._root = root
        self._codec = codec
        self._ops: FileOps = file_ops if file_ops is not None else OsFileOps()

    async def publish(self, version: WorkflowVersion) -> None:
        """Store a new version atomically; see the module docstring for the mechanism."""
        await asyncio.to_thread(self._publish, version)

    async def get(self, workflow_id: WorkflowId, version: int) -> WorkflowVersion | None:
        """One version, or None if it does not exist."""
        return await asyncio.to_thread(self._get, workflow_id, version)

    async def latest(self, workflow_id: WorkflowId) -> WorkflowVersion | None:
        """The highest-numbered version, or None if the workflow has none."""
        return await asyncio.to_thread(self._latest, workflow_id)

    async def versions(self, workflow_id: WorkflowId) -> tuple[int, ...]:
        """Every stored version number, ascending."""
        return await asyncio.to_thread(self._versions, self._directory(workflow_id))

    def _directory(self, workflow_id: str) -> Path:
        slug = parse_workflow_id(workflow_id)
        root = self._root.resolve()
        directory = root / slug
        if directory.is_symlink() or (directory.exists() and directory.resolve().parent != root):
            raise PolicyViolation(
                "workflow directory escapes the store root",
                workflow_id=slug,
                root=str(root),
            )
        return directory

    def _publish(self, version: WorkflowVersion) -> None:
        directory = self._directory(version.workflow_id)
        data = self._codec.encode(version)
        context = {"workflow_id": version.workflow_id, "version": version.version}
        existing = self._versions(directory)
        if version.parent_version is not None and version.parent_version not in existing:
            raise VersionConflict(
                f"cannot publish v{version.version}: its parent v{version.parent_version} "
                "is not stored",
                reason="parent_missing",
                **context,
            )
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            self._ops.sync_directory(directory.parent)

        final = directory / version_file_name(version.version)
        descriptor, temporary = self._ops.create_temp(directory, prefix=f".{final.name}.")
        try:
            try:
                self._ops.write_all(descriptor, data)
                self._ops.sync_file(descriptor)
            finally:
                self._ops.close(descriptor)
            try:
                self._ops.link_exclusive(temporary, final)
            except FileExistsError:
                if final.read_bytes() == data:
                    structlog.get_logger(__name__).info(
                        "workflow_version_already_published", **context
                    )
                    return
                structlog.get_logger(__name__).warning("workflow_version_conflict", **context)
                raise VersionConflict(
                    f"v{version.version} of {version.workflow_id} already exists",
                    reason="exists",
                    **context,
                ) from None
        finally:
            self._ops.remove(temporary)
        self._ops.sync_directory(directory)
        structlog.get_logger(__name__).info("workflow_version_published", **context)

    def _get(self, workflow_id: str, version: int) -> WorkflowVersion | None:
        directory = self._directory(workflow_id)
        path = directory / version_file_name(version)
        if path.is_symlink():
            raise PolicyViolation("version file is a symbolic link", path=str(path))
        if not path.is_file():
            return None
        with path.open("rb") as file:
            # One byte over the limit is enough for the codec to reject an oversized file.
            content = file.read(self._codec.max_bytes + 1)
        stored = self._codec.decode(content, source=str(path))
        if stored.workflow_id != workflow_id or stored.version != version:
            raise WorkflowValidationError(
                f"{path} holds {stored.workflow_id} v{stored.version}, "
                f"not {workflow_id} v{version}",
                issues=(
                    ValidationIssue(
                        (), "the file's workflow_id and version disagree with its path"
                    ),
                ),
                source=str(path),
            )
        return stored

    def _latest(self, workflow_id: str) -> WorkflowVersion | None:
        numbers = self._versions(self._directory(workflow_id))
        return self._get(workflow_id, numbers[-1]) if numbers else None

    def _versions(self, directory: Path) -> tuple[int, ...]:
        if not directory.is_dir():
            return ()
        numbers: list[int] = []
        for entry in directory.iterdir():
            if entry.name.startswith("."):
                # Temporary files of in-flight or interrupted publishes, and OS metadata.
                continue
            match = _VERSION_FILE.fullmatch(entry.name)
            number = int(match[1]) if match else -1
            if match is None or entry.name != version_file_name(number) or entry.is_symlink():
                raise _integrity_error(directory, f"unexpected entry '{entry.name}'")
            numbers.append(number)
        numbers.sort()
        if numbers != list(range(1, len(numbers) + 1)):
            raise _integrity_error(directory, f"version files are not contiguous from 1: {numbers}")
        return tuple(numbers)


def _integrity_error(directory: Path, problem: str) -> WorkflowValidationError:
    return WorkflowValidationError(
        f"workflow store directory {directory} is inconsistent: {problem}",
        issues=(
            ValidationIssue(
                (), f"{problem}; a workflow directory holds only v0001.yaml, v0002.yaml, ..."
            ),
        ),
        source=str(directory),
    )
