"""Identifiers are short lowercase slugs that cannot escape a path or collide by case."""

import pytest

from mendwork.engine.domain.identifiers import is_slug, parse_workflow_id
from mendwork.engine.errors import WorkflowValidationError
from tests.workflows import document, problems


@pytest.mark.parametrize("value", ["a", "download_report", "view_po_1042", "x" * 64])
def test_valid_slugs_are_accepted(value: str) -> None:
    assert is_slug(value)
    assert parse_workflow_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Download",
        "1report",
        "_report",
        "report_",
        "double__underscore",
        "kebab-case",
        "../escape",
        "a/b",
        "/etc",
        ".",
        "..",
        "report\n",
        "report\x00",
        "\u217eownload",
        "x" * 65,
    ],
)
def test_invalid_slugs_are_rejected(value: str) -> None:
    assert not is_slug(value)
    with pytest.raises(WorkflowValidationError, match="lowercase slug"):
        parse_workflow_id(value)


def test_an_invalid_workflow_id_in_a_document_is_explained() -> None:
    assert problems(document(workflow_id="Download-Report")) == [
        (
            "workflow_id",
            "must be a lowercase slug: letters, digits, and single underscores, starting with "
            "a letter, at most 64 characters",
        )
    ]
