"""Fixture workflows stay exactly their example, but for the one field each exists to change."""

from pathlib import Path
from typing import Final

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.enums import RiskLevel
from mendwork.engine.domain.workflow import WorkflowVersion
from tests.workflows import REPO_ROOT, load_example

APPROVAL_FIXTURE: Final = (
    REPO_ROOT / "tests" / "fixtures" / "workflows" / "download_report_approval.yaml"
)


def load(path: Path) -> WorkflowVersion:
    return WorkflowYamlCodec(max_bytes=1 << 20).decode(path.read_bytes(), source=str(path))


def test_the_approval_fixture_is_the_example_with_only_its_download_made_irreversible() -> None:
    example = load_example("download_report")
    fixture = load(APPROVAL_FIXTURE)

    changed = [
        (index, step.id)
        for index, (step, original) in enumerate(zip(fixture.steps, example.steps, strict=True))
        if step != original
    ]
    index = len(example.steps) - 1
    restored = fixture.steps[index].model_copy(update={"risk": example.steps[index].risk})

    assert changed == [(index, "download_csv")]
    assert (example.steps[index].risk, fixture.steps[index].risk) == (
        RiskLevel.SAFE,
        RiskLevel.IRREVERSIBLE,
    )
    assert restored == example.steps[index]
    assert fixture.model_copy(update={"steps": example.steps}) == example
