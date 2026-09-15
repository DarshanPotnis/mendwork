"""The words people read about versions, targets, checks, and heals (ADR 0013).

Shared by ``mendwork history``, ``diff``, ``import``, and ``rollback`` and by the run report, so
every place says the same thing the same way, in plain words rather than field names. Nothing a
person typed into a field is ever quoted: a literal value is described, not shown.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final

from mendwork.engine.domain.changes import ChangeRecord, HealChange, ManualEdit, Rollback
from mendwork.engine.domain.checkpoints import (
    Checkpoint,
    DownloadCompleted,
    ElementVisible,
    FieldHasValue,
    NoErrorBanner,
    ResponseReceived,
    TextPresent,
    UrlMatches,
)
from mendwork.engine.domain.enums import CheckpointKind, UrlMatchMode, VerificationStrength
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.selectors import (
    ByCss,
    ByLabel,
    ByPlaceholder,
    ByRole,
    ByTestId,
    ByText,
    Selector,
)
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.values import InputValue, LiteralValue, SecretValue, ValueRef

_ROLE_NOUNS: Final[Mapping[str, str]] = {
    "button": "button",
    "link": "link",
    "textbox": "text field",
    "searchbox": "search field",
    "checkbox": "checkbox",
    "radio": "radio button",
    "combobox": "drop-down list",
    "listbox": "list",
    "option": "option",
    "tab": "tab",
    "menuitem": "menu item",
    "switch": "switch",
    "spinbutton": "number field",
    "slider": "slider",
    "row": "row",
    "cell": "cell",
    "heading": "heading",
    "img": "image",
}
_TYPE_NOUNS: Final[Mapping[str, str]] = {
    "password": "password field",
    "date": "date field",
    "email": "email field",
    "number": "number field",
    "file": "file picker",
}
_TAG_NOUNS: Final[Mapping[str, str]] = {
    "a": "link",
    "button": "button",
    "input": "field",
    "textarea": "text field",
    "select": "drop-down list",
}
_RUNGS: Final[Mapping[int, str]] = {
    1: (
        "Repaired automatically with a selector built from its recorded details "
        "(rung 1, no AI model)"
    ),
    2: "Repaired automatically by similarity scoring (rung 2, no AI model)",
    3: "Repaired by an AI model (rung 3), which chose it from the closest elements on the page",
}
_KIND_WORDS: Final[Mapping[CheckpointKind, str]] = {
    CheckpointKind.URL_MATCHES: "the browser reached the recorded address",
    CheckpointKind.ELEMENT_VISIBLE: "the expected element appeared",
    CheckpointKind.TEXT_PRESENT: "the expected text appeared",
    CheckpointKind.DOWNLOAD_COMPLETED: "the file download finished",
    CheckpointKind.RESPONSE_RECEIVED: "the expected response arrived",
    CheckpointKind.NO_ERROR_BANNER: "no error message was shown",
    CheckpointKind.FIELD_HAS_VALUE: "the field held the typed value",
}


def quoted(text: str) -> str:
    """Text as a person reads it quoted."""
    return f'"{text}"'


def with_article(noun: str) -> str:
    """The noun with "a" or "an"."""
    return f"{'an' if noun[:1].lower() in 'aeiou' else 'a'} {noun}"


def element_kind(role: str | None, tag: str, input_type: str | None) -> str:
    """What an element is, as a person would say it: "a button", "a date field"."""
    if input_type is not None and input_type in _TYPE_NOUNS:
        noun = _TYPE_NOUNS[input_type]
    elif role is not None:
        noun = _ROLE_NOUNS.get(role, role)
    else:
        noun = _TAG_NOUNS.get(tag, f"<{tag}> element")
    return with_article(noun)


def fingerprint_kind(fingerprint: Fingerprint) -> str:
    """What a recorded element is."""
    role = fingerprint.role.value if fingerprint.role is not None else None
    return element_kind(role, fingerprint.tag, fingerprint.attributes.type)


def target_words(fingerprint: Fingerprint) -> str:
    """A recorded element in one phrase: its kind and its name."""
    name = fingerprint.accessible_name or fingerprint.text or fingerprint.label_text
    kind = fingerprint_kind(fingerprint)
    return f"{kind} named {quoted(name)}" if name else kind


def selector_words(selector: Selector) -> str:
    """How a selector finds its element, such as: by its test id "ledger-export"."""
    words = _selector_base(selector)
    if selector.within is not None:
        words += f", inside the element found {selector_words(selector.within)}"
    return words


def _selector_base(selector: Selector) -> str:
    match selector:
        case ByTestId():
            return f"by its test id {quoted(selector.value)}"
        case ByRole():
            kind = element_kind(selector.role.value, "", None)
            relation = "named" if selector.exact else "whose name contains"
            return f"as {kind} {relation} {quoted(selector.name)}"
        case ByLabel():
            return f"by its label {quoted(selector.value)}{_containing(selector.exact)}"
        case ByPlaceholder():
            return f"by its placeholder {quoted(selector.value)}{_containing(selector.exact)}"
        case ByText():
            return f"by its text {quoted(selector.value)}{_containing(selector.exact)}"
        case ByCss():
            if selector.value.startswith("#") and " " not in selector.value:
                return f"by its id {quoted(selector.value)}"
            return f"by the CSS selector {quoted(selector.value)}"


def _containing(exact: bool) -> str:
    return "" if exact else " (containing it)"


def value_words(value: ValueRef | None) -> str | None:
    """Where a step's value comes from, never the value itself."""
    match value:
        case None:
            return None
        case LiteralValue():
            return "a value written in the workflow"
        case InputValue():
            return f"the run input {quoted(value.name)}"
        case SecretValue():
            return f"the secret {quoted(value.name)}"


def checkpoint_words(checkpoint: Checkpoint) -> str:
    """What a check looks for, in words."""
    match checkpoint:
        case UrlMatches():
            relation = {
                UrlMatchMode.EXACT: "exactly",
                UrlMatchMode.PREFIX: "an address starting with",
                UrlMatchMode.REGEX: "an address matching",
            }[checkpoint.mode]
            return f"the browser went to {relation} {quoted(checkpoint.pattern)}"
        case ElementVisible():
            return f"an element appeared, found {selector_words(checkpoint.selector)}"
        case TextPresent():
            return f"the text {quoted(checkpoint.text)} appeared"
        case DownloadCompleted():
            return "the file download finished"
        case ResponseReceived():
            return (
                f"a response from an address matching {quoted(checkpoint.pattern)} arrived with "
                f"status {checkpoint.status_min} to {checkpoint.status_max}"
            )
        case NoErrorBanner():
            return "no error message was shown"
        case FieldHasValue():
            return "the field held the typed value"


def confirmed_by(step: Step | None, kinds: Sequence[CheckpointKind]) -> str:
    """The checks that proved a heal, the error-banner check left out when others passed too."""
    if step is not None and step.checkpoints:
        everything: list[Checkpoint] = list(step.checkpoints)
        proving = [item for item in everything if not isinstance(item, NoErrorBanner)]
        return "; ".join(checkpoint_words(item) for item in (proving or everything))
    listed: list[CheckpointKind] = list(kinds)
    proving_kinds = [kind for kind in listed if kind is not CheckpointKind.NO_ERROR_BANNER]
    return "; ".join(_KIND_WORDS[kind] for kind in (proving_kinds or listed))


def strength_sentence(strength: VerificationStrength, kinds: Sequence[CheckpointKind]) -> str:
    """How much a step's checks prove about which element was used."""
    match strength:
        case VerificationStrength.STRONG:
            return "That check is strong: it shows the right element was used."
        case VerificationStrength.WEAK if CheckpointKind.URL_MATCHES in kinds:
            return "That check is weak: another link to the same page would pass it too."
        case VerificationStrength.WEAK:
            return (
                "That check is weak: it shows a value landed in a field, not that it was the "
                "right field."
            )
        case VerificationStrength.NONE:
            return "No check shows which element was used."


def strength_adverb(strength: VerificationStrength) -> str:
    """How a step's checks verified it: "strongly", "weakly", or not at all."""
    match strength:
        case VerificationStrength.STRONG:
            return "strongly"
        case VerificationStrength.WEAK:
            return "weakly"
        case VerificationStrength.NONE:
            return "without a check that proves it"


def token_words(tokens: int, calls: int, unreported: int) -> str:
    """The tokens calls used; a provider that reported no counts is said so, never shown as 0."""
    if unreported == 0:
        return f"{tokens} tokens"
    if unreported >= calls:
        return "token counts not reported by this provider"
    return (
        f"{tokens} tokens for {calls - unreported} of {calls} calls "
        f"(token counts not reported for {unreported})"
    )


_NO_PRICE: Final = "no price in MENDWORK_MODEL_PRICES"


def cost_words(cost: Decimal, calls: int, unpriced: int, *, prefix: str = "") -> str:
    """What calls cost; a call with no configured price reads as unknown, never as a dollar figure.

    ``prefix`` goes before a known amount, such as "about ". The amount covers priced calls only.
    """
    if unpriced == 0:
        return f"{prefix}${cost:.4f}"
    if unpriced >= calls:
        return f"cost unknown ({_NO_PRICE})"
    return (
        f"{prefix}${cost:.4f} for {calls - unpriced} of {calls} calls "
        f"(cost unknown for {unpriced}, {_NO_PRICE})"
    )


def heal_reason(change: HealChange, step: Step | None) -> tuple[str, ...]:
    """Why a heal changed a step, as the paragraphs of a diff's or a report's "Why"."""
    kinds = list(change.checkpoints)
    first = (
        f"{_RUNGS[change.rung]} and confirmed by the step's check: {confirmed_by(step, kinds)}. "
        f"{strength_sentence(change.strength, kinds)}"
    )
    paragraphs = [first]
    numbers = score_words(change.score, change.threshold, change.margin, change.required_margin)
    if numbers is not None:
        paragraphs.append(numbers)
    model = change.model
    if model is not None:
        calls = "call" if model.calls == 1 else "calls"
        tokens = token_words(
            model.input_tokens + model.output_tokens, model.calls, model.unreported_token_calls
        )
        cost = cost_words(model.estimated_cost_usd, model.calls, model.unpriced_calls)
        paragraphs.append(
            f"Model: {model.provider} {quoted(model.model)}, {model.calls} {calls}, {tokens}, "
            f"{cost}."
        )
    if change.approval is not None:
        paragraphs.append(
            f"Approved by a person (proposal {change.approval.proposal_id}, audit entry "
            f"{change.approval.audit_sequence}) before it acted."
        )
    return tuple(paragraphs)


def score_words(
    score: float | None,
    threshold: float | None,
    margin: float | None,
    required_margin: float | None,
) -> str | None:
    """A heal's similarity and lead, with what each needed."""
    if score is None:
        return None
    words = f"Similarity {score:.2f}"
    if threshold is not None:
        words += f" ({threshold:.2f} needed)"
    if margin is not None:
        words += f", {margin:.2f} ahead of the next closest element"
        if required_margin is not None:
            words += f" ({required_margin:.2f} needed)"
    return words + "."


def change_summary(change: ChangeRecord | None, steps: Sequence[Step]) -> str:
    """One line on why a version exists."""
    match change:
        case None:
            return "First version"
        case ManualEdit():
            return f"Edited by hand: {change.summary}"
        case Rollback():
            return f"Rolled back by hand to v{change.restored_version}: {quoted(change.reason)}"
        case HealChange():
            index = next(
                (position for position, step in enumerate(steps) if step.id == change.step_id), 0
            )
            chosen = ", chosen by an AI model" if change.rung == 3 else ""
            approved = ", approved by a person" if change.approval is not None else ""
            return (
                f"Step {index + 1} {change.step_id} healed at rung {change.rung}{chosen}, "
                f"verified {strength_adverb(change.strength)}{approved}"
            )


def moment(at: datetime) -> str:
    """A UTC time as "15 Sep 2026 18:06 UTC"."""
    return f"{at.day} {at:%b %Y %H:%M} UTC"
