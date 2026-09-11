"""A WorkflowStore in memory, with the same rules as the file-system store."""

from mendwork.engine.domain.identifiers import WorkflowId, parse_workflow_id
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import VersionConflict


class InMemoryWorkflowStore:
    """Append-only versions in a dictionary; contract tests run it and the real store alike."""

    def __init__(self) -> None:
        self._workflows: dict[str, dict[int, WorkflowVersion]] = {}

    async def publish(self, version: WorkflowVersion) -> None:
        parse_workflow_id(version.workflow_id)
        stored = self._workflows.setdefault(version.workflow_id, {})
        context = {"workflow_id": version.workflow_id, "version": version.version}
        if version.parent_version is not None and version.parent_version not in stored:
            raise VersionConflict(
                "parent version is not stored", reason="parent_missing", **context
            )
        existing = stored.get(version.version)
        if existing is None:
            stored[version.version] = version
        elif existing != version:
            raise VersionConflict("version already exists", reason="exists", **context)

    async def get(self, workflow_id: WorkflowId, version: int) -> WorkflowVersion | None:
        return self._workflows.get(parse_workflow_id(workflow_id), {}).get(version)

    async def latest(self, workflow_id: WorkflowId) -> WorkflowVersion | None:
        numbers = await self.versions(workflow_id)
        return await self.get(workflow_id, numbers[-1]) if numbers else None

    async def versions(self, workflow_id: WorkflowId) -> tuple[int, ...]:
        return tuple(sorted(self._workflows.get(parse_workflow_id(workflow_id), {})))
