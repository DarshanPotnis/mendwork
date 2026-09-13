"""Naming run inputs and secrets after a recording stops: propose, then accept or override.

The recorder proposes, and the person decides:

- every secret field gets a secret name, defaulting to a slug of the field's name;
- the start URL, and any value that looks like an email or a username, is proposed as a
  run input with a name and a description; the person can accept both, type a name
  ("start_url") or a name and a description ("start_url: sign-in page of the portal"), or
  keep the value literal;
- ``--input NAME=VALUE`` decides in advance: every recorded literal equal to VALUE becomes
  input NAME, with the default description, and no proposal is shown for it.

Names are validated as they are chosen: a slug, and never the same name for an input and a
secret, or for two different input values. Descriptions must fit the format.
"""

import re
from collections.abc import Iterable, Sequence
from typing import Final

from pydantic import ValidationError

from mendwork.engine.domain.base import DomainModel, LiteralText, Text
from mendwork.engine.domain.credentials import tokenize
from mendwork.engine.domain.enums import ActionType, InputKind
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.identifiers import (
    SLUG_DESCRIPTION,
    InputName,
    InputNameField,
    SecretName,
    SecretNameField,
    is_slug,
)
from mendwork.engine.domain.limits import IDENTIFIER_MAX_LENGTH
from mendwork.engine.domain.recording import DraftStep, InputHint, LiteralDraft, SecretDraft
from mendwork.engine.domain.values import parse_input_value
from mendwork.engine.recording.input_descriptions import (
    default_input_description,
    description_problem,
    split_answer,
)

KEEP_LITERAL: Final = "-"
_EMAIL: Final = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_USERNAME_AUTOCOMPLETE: Final = frozenset({"username", "email"})
_USERNAME_WORDS: Final = frozenset({"email", "login", "mail", "user", "userid", "username"})
_ASCII_WORD: Final = re.compile(r"[a-z0-9]+")


class InputProposal(DomainModel):
    """A literal value the recorder suggests turning into a run input."""

    value: LiteralText
    kind: InputKind
    steps: tuple[int, ...]
    hint: InputHint
    default_name: InputNameField
    default_description: Text
    prompt: str
    """What the person is shown about the value; never a typed value itself."""


class SecretProposal(DomainModel):
    """A credential field whose secret needs a name."""

    step: int
    default_name: SecretNameField
    prompt: str


class InputDecision(DomainModel):
    """A run input and the steps whose values it supplies."""

    name: InputNameField
    kind: InputKind
    value: LiteralText
    """The recorded value, used when the recording is verified by replaying it."""
    steps: tuple[int, ...]
    description: Text | None = None


class SecretDecision(DomainModel):
    """The secret a credential field's step uses."""

    name: SecretNameField
    step: int


class Decisions(DomainModel):
    """Every naming decision for one recording."""

    inputs: tuple[InputDecision, ...] = ()
    secrets: tuple[SecretDecision, ...] = ()


def input_hint(target: Fingerprint | None, value: str) -> InputHint | None:
    """Why a fill's value should become an input: it looks like an email or a username."""
    if _EMAIL.fullmatch(value.strip()):
        return InputHint.EMAIL
    if target is None:
        return None
    autocomplete = set((target.attributes.autocomplete or "").lower().split())
    if autocomplete & _USERNAME_AUTOCOMPLETE:
        return InputHint.USERNAME
    if _USERNAME_WORDS & set(_words(_field_words(target))):
        return InputHint.USERNAME
    return None


def default_secret_name(target: Fingerprint) -> SecretName:
    """A secret name from the field: its name attribute, id, label, or accessible name."""
    return SecretName(_slug(_field_words(target), "password"))


class NamingSession:
    """The decisions for one recording, made one proposal at a time."""

    def __init__(self, steps: Sequence[DraftStep], overrides: Sequence[tuple[str, str]]) -> None:
        self._steps = tuple(steps)
        self._inputs: dict[str, InputDecision] = {}
        self._secrets: dict[int, str] = {}
        self._kept_literal: set[int] = set()
        self.warnings: list[str] = []
        self._apply_overrides(overrides)
        self.secret_proposals = self._propose_secrets()
        self.input_proposals = self._propose_inputs()

    def suggested_input_name(self, proposal: InputProposal) -> str:
        """The proposal's default name, made unique against the names chosen so far."""
        taken = set(self._secrets.values()) | {
            name
            for name, decision in self._inputs.items()
            if (decision.value, decision.kind) != (proposal.value, proposal.kind)
        }
        return _unique(proposal.default_name, taken)

    def suggested_input_answer(self, proposal: InputProposal) -> str:
        """What pressing Enter accepts: ``name: description``."""
        return f"{self.suggested_input_name(proposal)}: {proposal.default_description}"

    def suggested_secret_name(self, proposal: SecretProposal) -> str:
        """The proposal's default name, made unique against the input names chosen so far.

        Two credential fields may share a secret, so other secrets' names are not taken.
        """
        return _unique(proposal.default_name, set(self._inputs))

    def name_secret(self, proposal: SecretProposal, answer: str) -> str | None:
        """Use ``answer`` (or the default when empty) as the secret's name; a problem or None."""
        name = answer.strip() or self.suggested_secret_name(proposal)
        if not is_slug(name):
            return SLUG_DESCRIPTION
        if name in self._inputs:
            return f"'{name}' is already the name of an input"
        self._secrets[proposal.step] = name
        return None

    def name_input(self, proposal: InputProposal, answer: str) -> str | None:
        """Decide an input from ``name``, ``name: description``, an empty answer, or ``-``."""
        if answer.strip() == KEEP_LITERAL:
            self._kept_literal.update(proposal.steps)
            return None
        parsed = split_answer(answer)
        name = parsed.name or self.suggested_input_name(proposal)
        if not is_slug(name):
            return SLUG_DESCRIPTION
        if name in self._secrets.values():
            return f"'{name}' is already the name of a secret"
        existing = self._inputs.get(name)
        if existing is not None and (existing.value, existing.kind) != (
            proposal.value,
            proposal.kind,
        ):
            return f"'{name}' is already the name of an input with a different value"
        description = parsed.description
        if description is not None:
            problem = description_problem(description)
            if problem is not None:
                return problem
        elif existing is not None and existing.description is not None:
            description = existing.description
        else:
            description = proposal.default_description
        steps = tuple(sorted({*proposal.steps, *(existing.steps if existing else ())}))
        self._inputs[name] = InputDecision(
            name=InputName(name),
            kind=proposal.kind,
            value=proposal.value,
            steps=steps,
            description=description,
        )
        return None

    def decisions(self) -> Decisions:
        """Every decision; proposals nobody answered take their default names."""
        for secret in self.secret_proposals:
            if secret.step not in self._secrets:
                self.name_secret(secret, "")
        for proposal in self.input_proposals:
            decided = any(set(proposal.steps) <= set(d.steps) for d in self._inputs.values())
            if not decided and not set(proposal.steps) <= self._kept_literal:
                self.name_input(proposal, "")
        inputs = sorted(self._inputs.values(), key=lambda decision: decision.steps[0])
        secrets = [
            SecretDecision(name=SecretName(name), step=step)
            for step, name in sorted(self._secrets.items())
        ]
        return Decisions(inputs=tuple(inputs), secrets=tuple(secrets))

    def _apply_overrides(self, overrides: Sequence[tuple[str, str]]) -> None:
        for name, value in overrides:
            steps = tuple(
                step.index
                for step in self._steps
                if isinstance(step.value, LiteralDraft) and step.value.value == value
            )
            if not is_slug(name):
                self.warnings.append(f"--input {name}: the name {SLUG_DESCRIPTION}")
                continue
            if not steps:
                self.warnings.append(
                    f"--input {name}: no recorded value matched, so it was not used"
                )
                continue
            kind = self._override_kind(steps, value)
            first = self._steps[steps[0]]
            hint = first.value.hint if isinstance(first.value, LiteralDraft) else None
            try:
                parse_input_value(kind, value)
                self._inputs[name] = InputDecision(
                    name=InputName(name),
                    kind=kind,
                    value=value,
                    steps=steps,
                    description=default_input_description(first, value, kind, hint),
                )
            except (ValueError, ValidationError) as error:
                self.warnings.append(f"--input {name}: the value is not a valid {kind}: {error}")

    def _override_kind(self, steps: Iterable[int], value: str) -> InputKind:
        if any(self._steps[index].action is ActionType.NAVIGATE for index in steps):
            return InputKind.URL
        return InputKind.DATE if _ISO_DATE.fullmatch(value) else InputKind.TEXT

    def _propose_secrets(self) -> tuple[SecretProposal, ...]:
        return tuple(
            SecretProposal(
                step=step.index,
                default_name=step.value.proposed_name,
                prompt=f"{step.step_id} · {_field(step)} ({step.value.reason})",
            )
            for step in self._steps
            if isinstance(step.value, SecretDraft)
        )

    def _propose_inputs(self) -> tuple[InputProposal, ...]:
        overridden = {index for decision in self._inputs.values() for index in decision.steps}
        grouped: dict[tuple[str, InputKind], list[DraftStep]] = {}
        hints: dict[tuple[str, InputKind], InputHint] = {}
        for step in self._steps:
            value = step.value
            if (
                not isinstance(value, LiteralDraft)
                or value.hint is None
                or step.index in overridden
            ):
                continue
            kind = InputKind.URL if step.action is ActionType.NAVIGATE else InputKind.TEXT
            key = (value.value, kind)
            grouped.setdefault(key, []).append(step)
            hints.setdefault(key, value.hint)
        proposals: list[InputProposal] = []
        for (literal, kind), steps in grouped.items():
            hint = hints[(literal, kind)]
            first = steps[0]
            if hint is InputHint.START_URL:
                default, prompt = "start_url", f"{first.step_id} · start URL {literal}"
            else:
                words = _field_words(first.target) if first.target is not None else ""
                default = _slug(words, hint.value)
                prompt = f"{first.step_id} · {_field(first)} (looks like {_article(hint)})"
            proposals.append(
                InputProposal(
                    value=literal,
                    kind=kind,
                    steps=tuple(step.index for step in steps),
                    hint=hint,
                    default_name=InputName(default),
                    default_description=default_input_description(first, literal, kind, hint),
                    prompt=prompt,
                )
            )
        return tuple(proposals)


def _unique(base: str, taken: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in taken:
        candidate = f"{base[: IDENTIFIER_MAX_LENGTH - 4]}_{counter}"
        counter += 1
    return candidate


def _field_words(target: Fingerprint) -> str:
    attributes = target.attributes
    for words in (attributes.name, attributes.id, target.label_text, target.accessible_name):
        if words and _words(words):
            return words
    return ""


def _field(step: DraftStep) -> str:
    target = step.target
    name = (target.label_text or target.accessible_name) if target is not None else None
    return f"'{name}' field" if name else "unnamed field"


def _article(hint: InputHint) -> str:
    return "an email" if hint is InputHint.EMAIL else "a username"


def _words(text: str) -> list[str]:
    return [word for token in tokenize(text) for word in _ASCII_WORD.findall(token)]


def _slug(text: str, fallback: str) -> str:
    slug = "_".join(_words(text))[:40].strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"{fallback}_{slug}".strip("_") if slug else fallback
    return slug if is_slug(slug) else fallback
