"""The systems the benchmark compares, and how the scorecard describes them (ADR 0014).

Two are plain Playwright scripts, run by ``benchmarks.baselines.script_runner``; three are the
product, differing only in who chooses at Rung 3. No system is a mode of the product: the scripts
live here, and the product runs exactly as ``mendwork run`` configures it.
"""

from typing import Final, Literal

from benchmarks.chaos.models import ModelMode
from mendwork.engine.benchmark.results import SystemDescription, SystemKind

CSS_SELECTOR: Final = "css_selector"
ROLE_NAME: Final = "role_name"
LADDER_FREE: Final = "ladder_free"
LADDER_GROUND_TRUTH: Final = "ladder_ground_truth"
LADDER_MODEL: Final = "ladder_model"
SYSTEM_IDS: Final = (CSS_SELECTOR, ROLE_NAME, LADDER_FREE, LADDER_GROUND_TRUTH, LADDER_MODEL)
DEFAULT_SYSTEMS: Final = (CSS_SELECTOR, ROLE_NAME, LADDER_FREE, LADDER_GROUND_TRUTH)
SMOKE_SYSTEMS: Final = (LADDER_FREE, LADDER_GROUND_TRUTH)
ScriptKind = Literal["css_selector", "role_name"]

_SCRIPT_CAVEAT: Final = (
    "It is verified by the workflow's own checkpoints, which a plain script usually lacks, so it "
    "stops after a caught wrong action where an unasserted script would carry on: its wrong "
    "actions are a lower bound."
)


def is_script(system: str) -> bool:
    """Whether a system is a plain Playwright script."""
    return system in (CSS_SELECTOR, ROLE_NAME)


def script_kind(system: str) -> ScriptKind:
    """The locator a script system uses."""
    if system == CSS_SELECTOR:
        return "css_selector"
    if system == ROLE_NAME:
        return "role_name"
    raise ValueError(f"{system} is not a script system")


def model_mode(system: str) -> ModelMode:
    """Who chooses at Rung 3 for a ladder system."""
    modes: dict[str, ModelMode] = {
        LADDER_FREE: "none",
        LADDER_GROUND_TRUTH: "oracle",
        LADDER_MODEL: "configured",
    }
    if system not in modes:
        raise ValueError(f"{system} is not a ladder system")
    return modes[system]


def describe(system: str, *, model: str | None = None) -> SystemDescription:
    """The scorecard's description of a system; ``model`` names the configured model."""
    match system:
        case "css_selector":
            return SystemDescription(
                id=system,
                label="Recorded CSS selector (plain Playwright script)",
                short_label="CSS selector script",
                kind=SystemKind.SCRIPT,
                description=(
                    "Each step's recorded css selector through page.locator, with Playwright's "
                    "auto-waiting and strict mode, and nothing else: no identity check, no "
                    f"healing. {_SCRIPT_CAVEAT}"
                ),
            )
        case "role_name":
            return SystemDescription(
                id=system,
                label="Role + name locator (careful Playwright script)",
                short_label="Role + name script",
                kind=SystemKind.SCRIPT,
                description=(
                    "Each step's recorded role and accessible name through Playwright's "
                    "user-facing locators, or its label for a field without a role, with "
                    f"auto-waiting and strict mode and no healing. {_SCRIPT_CAVEAT}"
                ),
            )
        case "ladder_free":
            return SystemDescription(
                id=system,
                label="Mendwork, free rungs only",
                short_label="Mendwork, free rungs",
                kind=SystemKind.LADDER,
                description=(
                    "The product as shipped, with no model configured: recorded selectors, "
                    "alternate selectors, and similarity scoring, every heal verified."
                ),
                gated=True,
            )
        case "ladder_ground_truth":
            return SystemDescription(
                id=system,
                label="Mendwork + ground-truth chooser (upper bound, not a model)",
                short_label="Mendwork + ground truth",
                kind=SystemKind.LADDER,
                description=(
                    "The full ladder with Rung 3 answered from the portal's ground truth, only "
                    "when exactly one listed line reads like the real target: what the rules let "
                    "a perfect chooser heal. It is not a model, and its numbers are an upper bound."
                ),
                chooser="ground truth (upper bound)",
                gated=True,
            )
        case "ladder_model":
            if model is None:
                raise ValueError("the model system needs the configured model's name")
            return SystemDescription(
                id=system,
                label=f"Mendwork + {model} (local)",
                # A chart row fits 28 characters; a tag's quantization suffix is the part to drop.
                short_label=f"Mendwork + {model.split('-')[0]}"[:28],
                kind=SystemKind.LADDER,
                description=(
                    f"The full ladder with Rung 3 asking {model} through the product's own "
                    "provider adapter, prompt, parsing, and rules."
                ),
                chooser=model,
                gated=True,
            )
        case _:
            raise ValueError(f"unknown system {system}")
