"""One benchmark cell: a system running one workflow on one page state, and what came of it.

Outcomes and measurements are kept apart. ``steps``, ``run_status``, and ``model_calls`` must be
identical whenever the same cell runs again; timings and token counts may vary, and never enter the
outcomes digest.
"""

from decimal import Decimal
from typing import Annotated, Final, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from mendwork.engine.benchmark.outcomes import StepOutcome
from mendwork.engine.benchmark.truth import Label, MutationCategory, StopKind
from mendwork.engine.domain.base import DomainModel
from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.identifiers import StepIdField, WorkflowIdField

SystemId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
"""A benchmarked system: a script baseline or a Mendwork configuration."""


class ChaosCell(DomainModel):
    """A workflow on one chaos seed and level."""

    type: Literal["chaos"] = "chaos"
    workflow_id: WorkflowIdField
    level: int = Field(ge=0, le=5)
    seed: int = Field(ge=0, le=4_294_967_295)
    system: SystemId


class MutationCaseCell(DomainModel):
    """A workflow segment on a page with exactly one change."""

    type: Literal["single_mutation"] = "single_mutation"
    case_id: Label
    mutation: Label
    category: MutationCategory
    workflow_id: WorkflowIdField
    system: SystemId


class RealAppCell(DomainModel):
    """A workflow recorded on one release of an application, run on another."""

    type: Literal["real_app"] = "real_app"
    pair: Label
    workflow_id: WorkflowIdField
    system: SystemId


Cell = Annotated[ChaosCell | MutationCaseCell | RealAppCell, Field(discriminator="type")]


def cell_key(cell: ChaosCell | MutationCaseCell | RealAppCell) -> tuple[str, ...]:
    """A total order over cells, so results and digests never depend on run order."""
    match cell:
        case ChaosCell():
            return (
                "chaos",
                cell.system,
                cell.workflow_id,
                f"{cell.level:02d}",
                f"{cell.seed:010d}",
            )
        case MutationCaseCell():
            return ("single_mutation", cell.system, cell.case_id, cell.workflow_id)
        case RealAppCell():
            return ("real_app", cell.system, cell.pair, cell.workflow_id)


def cell_group(cell: ChaosCell | MutationCaseCell | RealAppCell) -> str:
    """The row a cell is reported under: its level, its mutation, or its pair."""
    match cell:
        case ChaosCell():
            return f"level {cell.level}"
        case MutationCaseCell():
            return cell.mutation
        case RealAppCell():
            return cell.pair


class StepTiming(DomainModel):
    """How long a reached step took."""

    step_id: StepIdField
    duration_ms: int | None = Field(default=None, ge=0)


class UsageMeasurement(DomainModel):
    """What a cell's model calls used; empty for a system without a model."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    unpriced_calls: int = Field(default=0, ge=0)


SUCCEEDED: Final = "succeeded"
UNRECORDED: Final = "unrecorded"
"""What a failure with no reason is called, so it is visible rather than absent."""


class RunFailure(DomainModel):
    """The step a run stopped at, and why.

    Classified outcomes cover only the steps that act on a control, so a run that stops at a
    navigate step would otherwise leave no trace of why it ended. Every failed run carries one of
    these, and a reason nobody recorded is named ``unrecorded`` rather than left out.
    """

    step_id: StepIdField
    action: ActionType
    stop: StopKind
    reason: Label = UNRECORDED
    targeted: bool = True
    """Whether the step acts on a control, and so also appears among the classified steps."""


class CellResult(DomainModel):
    """One cell's outcome and measurements."""

    cell: Cell
    run_status: Label
    steps: tuple[StepOutcome, ...]
    run_failure: RunFailure | None = None
    """Why the run stopped, when it did not succeed; None for a run that finished its steps."""
    model_calls: int = Field(default=0, ge=0)
    timings: tuple[StepTiming, ...] = ()
    usage: UsageMeasurement = UsageMeasurement()
    duration_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _a_run_that_stopped_says_where(self) -> Self:
        """No cell may report a failure the results cannot explain."""
        if (self.run_status != SUCCEEDED) != (self.run_failure is not None):
            raise ValueError(
                "a run that did not succeed, and only such a run, records the step it stopped at"
            )
        return self
