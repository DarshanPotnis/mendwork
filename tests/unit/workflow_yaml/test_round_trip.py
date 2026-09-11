"""Properties of encoding: lossless, byte-stable, and always accepted by the JSON Schema.

One property checks all three on the same generated version, because generating valid
workflows is the expensive part and the three claims are about the same encoding.
"""

import json

from hypothesis import given
from jsonschema import Draft202012Validator

from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.domain.documents import workflow_document
from mendwork.engine.domain.json_schema import workflow_json_schema
from mendwork.engine.domain.workflow import WorkflowVersion
from tests.strategies import workflow_versions

CODEC = WorkflowYamlCodec(max_bytes=1 << 20)
SCHEMA = Draft202012Validator(workflow_json_schema())


@given(workflow_versions())
def test_encoding_is_lossless_byte_stable_and_schema_valid(version: WorkflowVersion) -> None:
    encoded = CODEC.encode(version)
    decoded = CODEC.decode(encoded, source="generated.yaml")

    assert decoded == version, "model -> YAML -> model changed the version"
    assert CODEC.encode(decoded) == encoded, "dump(load(dump(x))) differs from dump(x)"
    document = json.loads(json.dumps(workflow_document(version)))
    assert [error.message for error in SCHEMA.iter_errors(document)] == []
