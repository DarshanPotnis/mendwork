"""The safety rules applied to one candidate, in a fixed order, with a reason a person can read.

A refused candidate is still scored and reported, so a reviewer sees what was close and why
it was not acted on.
"""

from pydantic import ValidationError

from mendwork.engine.domain.enums import SelectorStrategy
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import RejectionReason, SafetyRejection
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.steps import FillStep, Step
from mendwork.engine.healing.candidates import found_kind, recorded_kind
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.ports.element_types import ElementFacts
from mendwork.engine.recording.selectors import SELECTOR, css_selector
from mendwork.engine.safety.heal_kinds import compare_kinds
from mendwork.engine.safety.heal_policy import (
    credential_mismatch,
    has_effect_checkpoint,
    identifier_mismatch,
    introduced_danger,
    is_credential_step,
)
from mendwork.engine.safety.risk import RiskVocabulary


def safety_rejection(
    step: Step, fingerprint: Fingerprint, candidate: LiveCandidate, vocabulary: RiskVocabulary
) -> SafetyRejection | None:
    """The first safety rule that refuses the candidate, or None."""
    recorded_texts = (fingerprint.accessible_name, fingerprint.text, fingerprint.label_text)
    found_texts = (candidate.identity.name, candidate.facts.text, candidate.facts.label_text)
    danger = introduced_danger(recorded_texts, found_texts, vocabulary)
    if danger:
        words = ", ".join(f'"{word}"' for word in danger)
        return SafetyRejection(
            reason=RejectionReason.DANGER_WORD,
            detail=f"its name adds {words}, which the recorded name does not have",
        )
    identifiers = identifier_mismatch(recorded_texts, found_texts)
    if identifiers is not None:
        recorded, found = identifiers
        return SafetyRejection(
            reason=RejectionReason.IDENTIFIER_MISMATCH,
            detail=f"it names {', '.join(found)} where the recording named {', '.join(recorded)}",
        )
    kinds = compare_kinds(recorded_kind(fingerprint), found_kind(candidate))
    if not kinds.compatible:
        return SafetyRejection(
            reason=RejectionReason.KIND_CHANGED, detail=kinds.reason or "it is another kind"
        )
    if kinds.needs_effect_checkpoint and not has_effect_checkpoint(step.checkpoints):
        return SafetyRejection(
            reason=RejectionReason.KIND_CHANGED,
            detail=(
                f"it changed kind ({kinds.change}), and this step has no checkpoint that proves "
                "activating it has the same effect"
            ),
        )
    if isinstance(step, FillStep):
        return _credential_rejection(step, candidate.facts)
    return None


def mask_selector(facts: ElementFacts) -> Selector | None:
    """A selector that masks a field in screenshots: its test id, stable id, or name."""
    documents: list[dict[str, object]] = []
    if facts.data_testid:
        documents.append({"strategy": SelectorStrategy.TEST_ID, "value": facts.data_testid})
    css = css_selector(facts.tag, facts.id, facts.name)
    if css is not None:
        documents.append({"strategy": SelectorStrategy.CSS, "value": css})
    for document in documents:
        try:
            return SELECTOR.validate_python(document)
        except ValidationError:
            continue
    return None


def _credential_rejection(step: FillStep, facts: ElementFacts) -> SafetyRejection | None:
    credential = is_credential_step(step)
    if credential_mismatch(credential, facts.masked):
        detail = (
            "the step types a credential and this field shows what is typed"
            if credential
            else "the step types a plain value and this field is masked like a credential field"
        )
        return SafetyRejection(reason=RejectionReason.CREDENTIAL_MISMATCH, detail=detail)
    if credential and mask_selector(facts) is None:
        return SafetyRejection(
            reason=RejectionReason.CREDENTIAL_MISMATCH,
            detail=(
                "no selector can mask this field in screenshots, so a credential cannot be typed"
            ),
        )
    return None
