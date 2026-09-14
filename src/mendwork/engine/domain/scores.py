"""Candidate ids and scores, shared by heal reports and model evidence."""

from typing import Annotated

from pydantic import Field, StringConstraints

CandidateId = Annotated[str, StringConstraints(pattern=r"^c[1-9][0-9]{0,5}$")]
"""A candidate's place in its attempt's ranking: ``c1`` is the best-scoring element."""
Score = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
