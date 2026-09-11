"""Strict YAML decoding that remembers where every value came from.

PyYAML's loaders are permissive in ways that are wrong for configuration people edit by
hand: a repeated key silently keeps the last value, anchors and aliases allow
exponential expansion, and YAML 1.1 turns ``no`` into false and ``2026-01-01`` into a
date. This loader builds plain data from the parser's event stream itself, so it can:

- reject duplicate keys, anchors, aliases, explicit tags, non-text keys, and more than
  one document, each at its line;
- resolve unquoted scalars under a narrow rule set (null, true/false, decimal integers,
  and decimals with a point) that is a strict subset of what PyYAML's dumper treats as
  non-text, so any string the dumper leaves unquoted reads back as a string;
- record the line and column of every key and sequence item, so validation errors can
  point at the exact place in the file.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

import yaml
from pydantic import JsonValue
from yaml.events import (
    AliasEvent,
    DocumentEndEvent,
    DocumentStartEvent,
    Event,
    MappingEndEvent,
    MappingStartEvent,
    NodeEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
    StreamEndEvent,
    StreamStartEvent,
)

from mendwork.engine.errors import Location, ValidationIssue, WorkflowValidationError

# The workflow schema nests at most eight levels; this only stops a hostile file from
# exhausting the recursion limit before validation could reject it.
MAX_NESTING_DEPTH: Final = 32

_NULL: Final = re.compile(r"~|null|Null|NULL|")
_BOOL: Final = re.compile(r"true|True|TRUE|false|False|FALSE")
_INT: Final = re.compile(r"[-+]?(?:0|[1-9][0-9]*)")
# YAML 1.1 (and so PyYAML's dumper) only reads a sign before digits and a signed exponent
# as a float: "-.5" and "1.0e5" are strings there, so they must be strings here too.
_FLOAT: Final = re.compile(r"[-+]?[0-9]+\.[0-9]*(?:[eE][-+][0-9]+)?|\.[0-9]+(?:[eE][-+][0-9]+)?")


@dataclass(frozen=True, slots=True)
class Position:
    """A 1-based line and column in the source text."""

    line: int
    column: int


@dataclass(frozen=True, slots=True)
class LoadedDocument:
    """Decoded data plus the source position of every mapping key and sequence item."""

    data: JsonValue
    positions: Mapping[Location, Position]


class _Mark(Protocol):
    """A parser position; PyYAML has a pure-Python and a libyaml Mark with these fields."""

    @property
    def line(self) -> int: ...

    @property
    def column(self) -> int: ...


class _EventStream:
    """One-event lookahead over PyYAML's parser."""

    def __init__(self, text: str) -> None:
        # yaml.parse is unannotated in types-PyYAML (Any); it yields yaml.events.Event objects.
        self._events: Iterator[Event] = iter(yaml.parse(text, Loader=yaml.SafeLoader))
        self._next: Event | None = None

    def peek(self) -> Event:
        if self._next is None:
            self._next = next(self._events)
        return self._next

    def take(self) -> Event:
        event = self.peek()
        self._next = None
        return event


def _position(mark: _Mark | None) -> Position:
    if mark is None:
        return Position(1, 1)
    return Position(mark.line + 1, mark.column + 1)


def _problem(location: Location, message: str, mark: _Mark | None) -> WorkflowValidationError:
    position = _position(mark)
    issue = ValidationIssue(location, message, position.line, position.column)
    return WorkflowValidationError(f"invalid YAML: {message}", issues=(issue,))


def _resolve_scalar(event: ScalarEvent) -> JsonValue:
    if not event.implicit[0]:
        return event.value
    value = event.value
    if _NULL.fullmatch(value):
        return None
    if _BOOL.fullmatch(value):
        return value.lower() == "true"
    if _INT.fullmatch(value):
        return int(value)
    if _FLOAT.fullmatch(value):
        return float(value)
    return value


class _Composer:
    def __init__(self, text: str) -> None:
        self._events = _EventStream(text)
        self.positions: dict[Location, Position] = {}

    def compose(self) -> JsonValue:
        self._expect(StreamStartEvent)
        if isinstance(self._events.peek(), StreamEndEvent):
            raise _problem((), "the file is empty", self._events.peek().start_mark)
        self._expect(DocumentStartEvent)
        data = self._node((), depth=1)
        self._expect(DocumentEndEvent)
        extra = self._events.peek()
        if not isinstance(extra, StreamEndEvent):
            raise _problem(
                (), "a workflow file must contain exactly one YAML document", extra.start_mark
            )
        return data

    def _expect(self, event_type: type[Event]) -> None:
        event = self._events.take()
        if not isinstance(event, event_type):
            raise _problem(
                (), f"unexpected YAML structure ({type(event).__name__})", event.start_mark
            )

    def _check_node(self, location: Location, event: Event, depth: int) -> None:
        if isinstance(event, AliasEvent):
            raise _problem(location, "aliases (*name) are not allowed", event.start_mark)
        if isinstance(event, NodeEvent) and event.anchor is not None:
            raise _problem(location, "anchors (&name) are not allowed", event.start_mark)
        if (
            isinstance(event, ScalarEvent | SequenceStartEvent | MappingStartEvent)
            and event.tag is not None
        ):
            raise _problem(location, "tags (such as !!str) are not allowed", event.start_mark)
        if depth > MAX_NESTING_DEPTH:
            raise _problem(
                location, f"nests deeper than {MAX_NESTING_DEPTH} levels", event.start_mark
            )

    def _node(self, location: Location, depth: int) -> JsonValue:
        event = self._events.take()
        self._check_node(location, event, depth)
        self.positions.setdefault(location, _position(event.start_mark))
        if isinstance(event, ScalarEvent):
            return _resolve_scalar(event)
        if isinstance(event, SequenceStartEvent):
            return self._sequence(location, depth)
        if isinstance(event, MappingStartEvent):
            return self._mapping(location, depth)
        raise _problem(
            location, f"unexpected YAML structure ({type(event).__name__})", event.start_mark
        )

    def _sequence(self, location: Location, depth: int) -> list[JsonValue]:
        items: list[JsonValue] = []
        while not isinstance(self._events.peek(), SequenceEndEvent):
            items.append(self._node((*location, len(items)), depth + 1))
        self._events.take()
        return items

    def _mapping(self, location: Location, depth: int) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        while not isinstance(self._events.peek(), MappingEndEvent):
            key_event = self._events.take()
            self._check_node(location, key_event, depth + 1)
            key = _resolve_scalar(key_event) if isinstance(key_event, ScalarEvent) else None
            if not isinstance(key, str):
                raise _problem(location, "mapping keys must be plain text", key_event.start_mark)
            key_location = (*location, key)
            if key in result:
                first = self.positions[key_location]
                raise _problem(
                    key_location,
                    f"duplicate key '{key}' (first defined at line {first.line})",
                    key_event.start_mark,
                )
            self.positions[key_location] = _position(key_event.start_mark)
            result[key] = self._node(key_location, depth + 1)
        self._events.take()
        return result


def load_yaml(content: bytes, *, max_bytes: int) -> LoadedDocument:
    """Decode a YAML workflow file strictly; every failure names a line where there is one."""
    if len(content) > max_bytes:
        raise WorkflowValidationError(
            f"the file is {len(content)} bytes, over the {max_bytes}-byte limit",
            issues=(ValidationIssue((), f"the file is larger than the {max_bytes}-byte limit"),),
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        line = content.count(b"\n", 0, error.start) + 1
        issue = ValidationIssue((), f"is not valid UTF-8 (byte {error.start})", line, None)
        raise WorkflowValidationError("the file is not valid UTF-8", issues=(issue,)) from None
    composer = _Composer(text)
    try:
        data = composer.compose()
    except yaml.MarkedYAMLError as error:
        message = f"YAML syntax error: {error.problem or error.context or 'unreadable document'}"
        raise _problem((), message, error.problem_mark or error.context_mark) from None
    except yaml.YAMLError as error:
        issue = ValidationIssue((), f"YAML syntax error: {error}")
        raise WorkflowValidationError("the file is not readable YAML", issues=(issue,)) from None
    return LoadedDocument(data, composer.positions)
