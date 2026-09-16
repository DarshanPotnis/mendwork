"""A person's labels for one release, and the approval that freezes them (ADR 0014).

Ground truth on a real application cannot come from the application, so it comes from a person: for
every step of the recorded workflow, either the control it must reach on this release, or that no
correct action exists. The labels are written before Mendwork runs on the release, approved against
the screenshots of a walk that performs the workflow with them alone, and frozen by their digest.
Scoring refuses labels that changed after the approval, so a label can never be tuned to a result.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from mendwork.engine.benchmark.truth import Expectation, StepTruth

RELEASE_CHANGE: Final = "changed_by_the_release"
"""What a changed control's difference is called in the results, as a chaos mutation is named."""


class Expect(StrEnum):
    """What a person says a step should do on this release."""

    ACT = "act"
    ABSTAIN = "abstain"


class StepLabel(BaseModel):
    """One step's label: the control to reach, or why there is none."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str = Field(min_length=1, max_length=100)
    expect: Expect
    selector: str | None = Field(default=None, min_length=1, max_length=400)
    """A Playwright selector for the control on this release; None when the step must abstain."""
    changed: bool = False
    """Whether the release changed this control from the one the workflow recorded."""
    note: str = Field(min_length=1, max_length=400)
    """Why this is the right control, or why there is none: the person's reasoning."""

    @model_validator(mode="after")
    def _selector_matches_expectation(self) -> Self:
        if (self.expect is Expect.ACT) != (self.selector is not None):
            raise ValueError(f"{self.step_id}: a step to act on needs a selector, and only it")
        return self


class PairLabels(BaseModel):
    """Every label for one release of one pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair: str = Field(min_length=1, max_length=100)
    release: str = Field(min_length=1, max_length=100)
    workflow_id: str = Field(min_length=1, max_length=100)
    labelled_on: str = Field(min_length=1, max_length=40)
    method: str = Field(min_length=1, max_length=600)
    """How the labels were written, which must never be "from Mendwork's output"."""
    steps: tuple[StepLabel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_label_per_step(self) -> Self:
        ids = [step.step_id for step in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("each step is labelled once")
        return self

    @property
    def digest(self) -> str:
        """The labels' SHA-256, over the content a person approved."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def selectors(self) -> dict[str, str]:
        """The control for every step that must act."""
        return {step.step_id: step.selector for step in self.steps if step.selector is not None}

    def truths(self) -> dict[str, StepTruth]:
        """The labels as the engine's ground truth: one truth per labelled step."""
        return {
            step.step_id: StepTruth(
                step_id=step.step_id,
                target_key=step.step_id,
                expectation=(Expectation.ACT if step.expect is Expect.ACT else Expectation.ABSTAIN),
                changes=(RELEASE_CHANGE,) if step.changed else (),
            )
            for step in self.steps
        }


class LabelApproval(BaseModel):
    """The person's approval of a set of labels, against the walk's screenshots."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    labels_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approved_by: str = Field(min_length=1, max_length=200)
    approved_at: AwareDatetime
    walked: tuple[str, ...] = Field(min_length=1)
    """The steps the labels walk performed with the labels alone, in order."""
    screenshots: Mapping[str, str] = Field(default_factory=dict)
    """One screenshot digest per walked step, as the person saw it."""


class LabelsNotApprovedError(RuntimeError):
    """The labels have no approval, or have changed since it was given."""


def load_labels(path: Path) -> PairLabels:
    """The labels for one release, validated."""
    return PairLabels.model_validate_json(path.read_text(encoding="utf-8"))


def load_approval(path: Path) -> LabelApproval:
    """The approval that freezes a set of labels."""
    return LabelApproval.model_validate_json(path.read_text(encoding="utf-8"))


def approved(labels: PairLabels, approval: LabelApproval | None) -> LabelApproval:
    """The approval, if it is for exactly these labels; otherwise nothing may be scored."""
    if approval is None:
        raise LabelsNotApprovedError(
            f"{labels.pair} {labels.release}: the labels are not approved; run the labels walk and "
            "have a person approve each label against its screenshot before scoring"
        )
    if approval.labels_digest != labels.digest:
        raise LabelsNotApprovedError(
            f"{labels.pair} {labels.release}: the labels changed after they were approved "
            f"({approval.labels_digest} approved, {labels.digest} now); walk and approve them again"
        )
    return approval


def write_approval(path: Path, approval: LabelApproval) -> None:
    """Record an approval beside its labels."""
    path.write_text(approval.model_dump_json(indent=2) + "\n", encoding="utf-8")


def approval_for(
    labels: PairLabels,
    *,
    by: str,
    at: datetime,
    walked: tuple[str, ...],
    screenshots: Mapping[str, str],
) -> LabelApproval:
    """An approval of these labels as they are now."""
    return LabelApproval(
        labels_digest=labels.digest,
        approved_by=by,
        approved_at=at,
        walked=walked,
        screenshots=screenshots,
    )
