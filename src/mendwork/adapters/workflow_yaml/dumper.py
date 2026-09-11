"""Deterministic YAML encoding for workflow documents.

The same document always produces the same bytes: keys keep the model's field order,
nothing is aliased, lines never wrap, and sequences are indented under their key. Strings
that any YAML 1.1 reader would take for another type are quoted, because PyYAML's dumper
resolves with the full YAML 1.1 rules.
"""

from typing import Final

import yaml
from pydantic import JsonValue

# Wrapping long strings across lines is legal YAML, but it makes regexes and URLs
# unreadable and diffs noisy; the format's own length limits keep lines bounded.
_NO_WRAP: Final = 1 << 20


class _WorkflowDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: object) -> bool:
        return True

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        # Indent sequences under their key ("steps:\n  - id: ...") instead of flush with it.
        super().increase_indent(flow, indentless=False)


def dump_yaml(document: JsonValue) -> bytes:
    """Encode a document as UTF-8 YAML with a trailing newline."""
    text = yaml.dump(
        document,
        Dumper=_WorkflowDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        indent=2,
        width=_NO_WRAP,
    )
    return text.encode("utf-8")
