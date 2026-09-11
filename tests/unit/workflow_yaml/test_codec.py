"""The codec reports common mistakes with file, line, path, and a plain message."""

import textwrap

import pytest

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.errors import UnsupportedSchemaVersion, WorkflowValidationError
from tests.workflows import EXAMPLE_IDS, example_path, load_example

CODEC = WorkflowYamlCodec(max_bytes=1 << 20)

VALID = textwrap.dedent(
    """\
    schema_version: 1
    workflow_id: demo
    version: 1
    created_at: 2026-09-11T08:30:00Z
    secrets: [portal_password]
    steps:
      - id: fill_password
        intent: Fill the 'Password' field
        action: fill
        risk: caution
        target:
          tag: input
          attributes: {type: password}
          structural_path: form > input
          selectors:
            - {strategy: label, value: Password}
        value: {kind: secret, name: portal_password}
      - id: sign_in
        intent: Click the 'Sign in' button
        action: click
        risk: caution
        target:
          tag: button
          structural_path: form > button
          selectors:
            - {strategy: role_name, role: button, name: Sign in}
    """
)


def issues_for(text: str) -> list[str]:
    with pytest.raises(WorkflowValidationError) as caught:
        CODEC.decode(text.encode(), source="broken.yaml")
    assert caught.value.context["source"] == "broken.yaml"
    return [
        f"{issue.line}:{issue.column} {issue.path} ({issue.step_id}): {issue.message}"
        for issue in caught.value.issues
    ]


def test_the_valid_document_decodes() -> None:
    assert CODEC.decode(VALID.encode(), source="demo.yaml").workflow_id == "demo"


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "value: {kind: secret, name: portal_password}",
            "value: {kind: literal, value: hunter2}",
            [
                "17:5 steps[0].value (fill_password): the target looks like a password or secret "
                'field (its type is "password"), so its value must be a secret reference such as '
                "{kind: secret, name: ...}, not a literal value"
            ],
        ),
        (
            "risk: caution\n    target:\n      tag: button",
            "risk: careful\n    target:\n      tag: button",
            ["21:5 steps[1].risk (sign_in): must be one of: 'safe', 'caution' or 'irreversible'"],
        ),
        (
            "{strategy: label, value: Password}",
            "{stratgy: label, value: Password}",
            [
                "16:11 steps[0].target.selectors[0] (fill_password): needs a 'strategy' field; "
                "found 'stratgy', did you mean 'strategy'?"
            ],
        ),
        (
            "name: Sign in}",
            "name: Sign in, exact: yes}",
            ["26:62 steps[1].target.selectors[0].exact (sign_in): must be true or false"],
        ),
        ("\nversion: 1", "\nversion: one", ["3:1 version (None): must be a whole number"]),
        (
            "created_at: 2026-09-11T08:30:00Z",
            "created_at: 11/09/2026",
            ["4:1 created_at (None): must be a UTC timestamp, such as 2026-09-11T08:30:00Z"],
        ),
        (
            "  - id: sign_in\n",
            "  - id: sign_in\n    value: {kind: literal, value: x}\n",
            [
                "19:5 steps[1].value (sign_in): 'value' is not a field of a click step; allowed: "
                "id, intent, action, risk, target, checkpoints"
            ],
        ),
        (
            "secrets: [portal_password]",
            "secrets: [portal_password, unused_token]",
            ["5:28 secrets[1] (None): secret 'unused_token' is declared but no step uses it"],
        ),
        (
            "name: portal_password}",
            "name: portal_pasword}",
            [
                "5:11 secrets[0] (None): secret 'portal_password' is declared but no step uses it",
                "17:27 steps[0].value.name (fill_password): secret 'portal_pasword' is not "
                "declared under secrets; did you mean 'portal_password'?",
            ],
        ),
        (
            "  - id: sign_in",
            "  - id: fill_password",
            [
                "18:5 steps[1].id (fill_password): duplicate step id 'fill_password' (already "
                "used by steps[0])"
            ],
        ),
        ("workflow_id: demo\n", "", ["1:1 workflow_id (None): is required"]),
    ],
)
def test_common_mistakes_are_located_and_explained(old: str, new: str, expected: list[str]) -> None:
    assert old in VALID
    assert issues_for(VALID.replace(old, new, 1)) == expected


def test_issues_are_sorted_by_line() -> None:
    broken = VALID.replace("risk: caution", "risk: none").replace(
        "workflow_id: demo", "workflow_id: Demo"
    )

    lines = [int(issue.split(":")[0]) for issue in issues_for(broken)]

    assert lines == sorted(lines)
    assert len(lines) == 3


def test_an_unsupported_schema_version_keeps_its_type_and_gains_its_source() -> None:
    with pytest.raises(UnsupportedSchemaVersion) as caught:
        CODEC.decode(
            VALID.replace("schema_version: 1", "schema_version: 2").encode(), source="new.yaml"
        )

    assert caught.value.context["source"] == "new.yaml"
    assert caught.value.context["found"] == 2
    [issue] = caught.value.issues
    assert (issue.line, issue.column, issue.path) == (1, 1, "schema_version")


def test_yaml_problems_carry_the_source_too() -> None:
    with pytest.raises(WorkflowValidationError) as caught:
        CODEC.decode(b"a: 1\na: 2\n", source="dupes.yaml")

    assert caught.value.context["source"] == "dupes.yaml"


@pytest.mark.parametrize("workflow_id", EXAMPLE_IDS)
def test_encoding_is_canonical_for_the_examples(workflow_id: str) -> None:
    example = load_example(workflow_id)

    encoded = CODEC.encode(example)

    assert CODEC.decode(encoded, source="canonical.yaml") == example
    assert CODEC.encode(CODEC.decode(encoded, source="canonical.yaml")) == encoded
    assert encoded.endswith(b"\n")
    assert example_path(workflow_id).read_bytes() != encoded  # hand-written files keep comments
