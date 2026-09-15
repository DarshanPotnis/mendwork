"""What Rung 3 needs: a model, the rung's limits, and each run's own budget.

An app builds one ModelRung from Settings. Every run makes its own ModelChooser, so one run's
call count and usage totals never reach another run.
"""

from dataclasses import dataclass

from pydantic import Field

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.ports.clock import Clock
from mendwork.engine.ports.model import ModelPort
from mendwork.engine.ports.usage_ledger import UsageLedger
from mendwork.engine.safety.budgets import BudgetLimits, RunModelBudget


class ModelChoiceConfig(DomainModel):
    """How Rung 3 asks a model."""

    candidates_k: int = Field(ge=1)
    """How many eligible candidates, best first, a model is shown."""
    timeout_ms: int = Field(ge=1)
    """One model call, retries included, capped by the heal deadline."""
    provider: str = Field(min_length=1)
    """The provider's name, for evidence when a call produced no usage of its own."""
    model: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ModelRung:
    """A configured model, its limits, and the ledger that keeps the daily count."""

    model: ModelPort
    config: ModelChoiceConfig
    limits: BudgetLimits
    ledger: UsageLedger

    def for_run(self, clock: Clock, *, reserved: int = 0) -> "ModelChooser":
        """A chooser with the run's own budget; a resumed run counts its earlier calls."""
        budget = RunModelBudget(
            limits=self.limits, ledger=self.ledger, clock=clock, reserved=reserved
        )
        return ModelChooser(model=self.model, config=self.config, budget=budget)


@dataclass(frozen=True, slots=True)
class ModelChooser:
    """Rung 3's model within one run."""

    model: ModelPort
    config: ModelChoiceConfig
    budget: RunModelBudget
