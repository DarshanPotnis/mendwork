"""Turning a model's reply text into a ChoiceResult, the same way for every provider.

The engine's strict parser decides whether a reply has the required shape. A reply that does
not, or that the provider cut off or withheld, raises ModelOutputInvalid with the problem in
fixed words, the start of the reply for a repair request, and the call's usage.
"""

from mendwork.engine.domain.model_evidence import ModelUsage
from mendwork.engine.errors import ModelOutputInvalid
from mendwork.engine.healing.choice import ChoiceProblem, parse_choice
from mendwork.engine.healing.prompt import REPAIR_EXCERPT_MAX_CHARS
from mendwork.engine.ports.model_types import ChoiceResult


def choice_result(
    text: str, usage: ModelUsage, *, problem: ChoiceProblem | None = None
) -> ChoiceResult:
    """The reply as a result, or ModelOutputInvalid naming what was wrong.

    ``problem`` is a problem the provider already reported, such as a reply cut off at the
    token limit, which takes precedence over parsing.
    """
    found = problem if problem is not None else parse_choice(text)
    if isinstance(found, ChoiceProblem):
        raise ModelOutputInvalid(
            f"the model's reply could not be used: {found.value}",
            problem=found.value,
            excerpt=text[:REPAIR_EXCERPT_MAX_CHARS],
            usage=usage,
        )
    return ChoiceResult(
        choice=found.choice, confidence=found.confidence, reason=found.reason, usage=usage
    )
