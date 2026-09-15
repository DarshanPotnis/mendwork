"""Run ids and artifact names: how records, workflow versions, and reports refer to a run's files.

Kept apart from the run record itself so a workflow version's change record can point at the run
and evidence that produced it without importing everything a run record holds.
"""

import re
from typing import Annotated, Final, NewType

from pydantic import StringConstraints

_RUN_ID: Final = r"\d{8}T\d{6}Z-[0-9a-f]{8}"
_ARTIFACT_SEGMENT: Final = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
_ARTIFACT_NAME: Final = rf"{_ARTIFACT_SEGMENT}(?:/{_ARTIFACT_SEGMENT}){{0,3}}"

RunId = NewType("RunId", str)
"""Sortable and path-safe, such as ``20260911T141502Z-7c1e09ab``: UTC start time plus randomness."""
ArtifactName = NewType("ArtifactName", str)
"""A relative path inside one run's artifacts, such as ``steps/04_sign_in.png``.

Every segment starts with a letter or digit, so ``.`` and ``..`` cannot occur and a name
can never leave its run's directory.
"""

RunIdField = Annotated[RunId, StringConstraints(pattern=f"^{_RUN_ID}$")]
ArtifactNameField = Annotated[ArtifactName, StringConstraints(pattern=f"^{_ARTIFACT_NAME}$")]

# fullmatch, not "$": in Python "$" also matches before a trailing newline.
_RUN_ID_RE: Final = re.compile(_RUN_ID)
_ARTIFACT_NAME_RE: Final = re.compile(_ARTIFACT_NAME)


def parse_run_id(value: str) -> RunId:
    """Validate an untrusted string as a run id."""
    if _RUN_ID_RE.fullmatch(value) is None:
        raise ValueError("a run id looks like 20260911T141502Z-7c1e09ab")
    return RunId(value)


def parse_artifact_name(value: str) -> ArtifactName:
    """Validate an untrusted string as an artifact name."""
    if _ARTIFACT_NAME_RE.fullmatch(value) is None:
        raise ValueError(
            "an artifact name is 1 to 4 '/'-separated segments of letters, digits, '.', '_' "
            "and '-', each starting with a letter or digit"
        )
    return ArtifactName(value)
