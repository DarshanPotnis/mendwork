"""Where each piece of a run's evidence is stored, relative to the run's artifacts.

::

    run.json
    workflow.json                       the exact version the run executes
    steps/004_sign_in.png               a screenshot after every step
    steps/004_sign_in.segment2.png      the same step, run again when an approval resumed the run
    failure/004_sign_in.dom.html        the DOM when the run failed
    failure/trace.zip                   the trace, unless it could hold a secret
    downloads/shipments_2026-02-10_to_2026-04-20.csv

A resume's evidence is named for its segment, so it never replaces the evidence a person saw when
they approved.
"""

import re
from collections.abc import Collection
from typing import Final

from mendwork.engine.domain.runs import ArtifactName, parse_artifact_name

RUN_RECORD: Final = parse_artifact_name("run.json")
WORKFLOW_SNAPSHOT: Final = parse_artifact_name("workflow.json")
"""The exact workflow version a run executes, so a resume never reads a file that has changed."""
FIRST_SEGMENT: Final = 1
"""A run's first execution; each resume is the next segment."""
TRACE: Final = parse_artifact_name("failure/trace.zip")
_UNSAFE: Final = re.compile(r"[^A-Za-z0-9._-]+")
_FILENAME_MAX: Final = 120
_FALLBACK_FILENAME: Final = "download"


def step_label(index: int, step_id: str) -> str:
    """A step's sortable label: its one-based position, zero-padded, and its id."""
    return f"{index + 1:03d}_{step_id}"


def screenshot_name(index: int, step_id: str, segment: int = FIRST_SEGMENT) -> ArtifactName:
    """The screenshot taken when a step ends."""
    return parse_artifact_name(f"steps/{_in_segment(step_label(index, step_id), segment)}.png")


def dom_snapshot_name(index: int, step_id: str, segment: int = FIRST_SEGMENT) -> ArtifactName:
    """The DOM snapshot taken when a step fails."""
    label = _in_segment(step_label(index, step_id), segment)
    return parse_artifact_name(f"failure/{label}.dom.html")


def trace_name(segment: int = FIRST_SEGMENT) -> ArtifactName:
    """The trace kept when a segment of a run fails."""
    return parse_artifact_name(f"failure/{_in_segment('trace', segment)}.zip")


def safe_filename(suggested: str) -> str:
    """A site-suggested filename reduced to a safe, single path segment.

    Sites choose download names, so a name like ``../../.bashrc`` must not choose where
    the file goes.
    """
    base = suggested.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE.sub("_", base).lstrip("._-")[:_FILENAME_MAX]
    return cleaned or _FALLBACK_FILENAME


def download_name(suggested: str, index: int, step_id: str, taken: Collection[str]) -> ArtifactName:
    """Where a download is kept: its own name, prefixed by the step if that name is taken."""
    filename = safe_filename(suggested)
    name = f"downloads/{filename}"
    if name in taken:
        name = f"downloads/{step_label(index, step_id)}_{filename}"[: len("downloads/") + 128]
    return parse_artifact_name(name)


def _in_segment(label: str, segment: int) -> str:
    return label if segment == FIRST_SEGMENT else f"{label}.segment{segment}"
