"""Reading and writing workflow versions as YAML files."""

from mendwork.adapters.workflow_yaml.dumper import dump_yaml
from mendwork.adapters.workflow_yaml.loader import LoadedDocument, Position, load_yaml
from mendwork.engine.domain.documents import parse_workflow_document, workflow_document
from mendwork.engine.domain.workflow import WorkflowVersion
from mendwork.engine.errors import ValidationIssue, WorkflowValidationError


class WorkflowYamlCodec:
    """Decodes and validates workflow YAML, and encodes versions canonically.

    Decoding failures carry every issue with its line and column and the source name, so
    a person can fix the whole file in one pass.
    """

    def __init__(self, *, max_bytes: int) -> None:
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        """The largest document, in bytes, this codec will decode."""
        return self._max_bytes

    def decode(self, content: bytes, *, source: str) -> WorkflowVersion:
        """Parse and validate a workflow file's bytes; ``source`` names it in errors."""
        try:
            loaded = load_yaml(content, max_bytes=self._max_bytes)
        except WorkflowValidationError as error:
            raise _from_source(error, error.issues, source) from error
        try:
            return parse_workflow_document(loaded.data)
        except WorkflowValidationError as error:
            raise _from_source(error, _position_issues(error, loaded), source) from error

    def encode(self, version: WorkflowVersion) -> bytes:
        """The canonical bytes of a version: decoding them yields an equal version."""
        return dump_yaml(workflow_document(version))


def _from_source(
    error: WorkflowValidationError, issues: tuple[ValidationIssue, ...], source: str
) -> WorkflowValidationError:
    # Same type, so an UnsupportedSchemaVersion stays one after gaining its source.
    context = {key: value for key, value in error.context.items() if key != "issues"}
    return type(error)(error.message, issues=issues, source=source, **context)


def _position_issues(
    error: WorkflowValidationError, loaded: LoadedDocument
) -> tuple[ValidationIssue, ...]:
    positioned = [_with_position(issue, loaded) for issue in error.issues]
    positioned.sort(key=lambda issue: (issue.line or 0, issue.column or 0))
    return tuple(positioned)


def _with_position(issue: ValidationIssue, loaded: LoadedDocument) -> ValidationIssue:
    position = _nearest_position(issue, loaded)
    return ValidationIssue(
        issue.location,
        issue.message,
        position.line,
        position.column,
        step_id=issue.step_id if issue.step_id is not None else _step_id(issue, loaded),
    )


def _nearest_position(issue: ValidationIssue, loaded: LoadedDocument) -> Position:
    # A missing field has no position of its own; its closest existing ancestor does.
    location = issue.location
    for length in range(len(location), -1, -1):
        position = loaded.positions.get(location[:length])
        if position is not None:
            return position
    return Position(1, 1)


def _step_id(issue: ValidationIssue, loaded: LoadedDocument) -> str | None:
    location = issue.location
    if len(location) < 2 or location[0] != "steps" or not isinstance(location[1], int):
        return None
    steps = loaded.data.get("steps") if isinstance(loaded.data, dict) else None
    if isinstance(steps, list) and location[1] < len(steps):
        step = steps[location[1]]
        if isinstance(step, dict) and isinstance(step.get("id"), str):
            return str(step["id"])
    return None
