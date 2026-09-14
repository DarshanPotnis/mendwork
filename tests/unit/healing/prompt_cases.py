"""Canonical Rung 3 prompts, from made-up applications, shared by the prompt tests.

Nothing here comes from the chaos portal: a ledger's export button, an invoice form's customer
reference, and a vault passcode (a field without an ARIA role).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.model_evidence import ShownCandidate
from mendwork.engine.domain.steps import Step
from mendwork.engine.healing.prompt import describe_candidate, render_messages
from mendwork.engine.ports.model_types import ChatMessage
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.unit.healing.builders import export_button, live, reference_field, step
from tests.workflows import click_step, fill_step


@dataclass(frozen=True, slots=True)
class PromptCase:
    """A step, its recorded fingerprint, and the numbered candidates a model is shown."""

    step: Step
    fingerprint: Fingerprint
    shown: tuple[ShownCandidate, ...]


def shown(
    number: int,
    candidate_id: str,
    fingerprint: Fingerprint,
    similarity: float,
    *,
    identity: Mapping[str, object] | None = None,
    facts: Mapping[str, object] | None = None,
    scrubber: SecretScrubber | None = None,
) -> ShownCandidate:
    """A numbered candidate like the recorded element, with any identity or facts changed."""
    candidate = live(fingerprint, identity=identity, facts=facts)
    return ShownCandidate(
        number=number,
        candidate=candidate_id,
        description=describe_candidate(candidate, scrubber or SecretScrubber()),
        similarity=similarity,
    )


EXPORT: Final = export_button()
REFERENCE: Final = reference_field()
PASSCODE: Final = Fingerprint.model_validate(
    {
        "tag": "input",
        "accessible_name": "Vault passcode",
        "label_text": "Vault passcode",
        "attributes": {"id": "passcode", "name": "passcode", "type": "password"},
        "nearby_text": ["Unlock the vault"],
        "structural_path": "main > form > div > input",
        "selectors": [{"strategy": "css", "value": "#passcode"}],
    }
)

CANONICAL: Final = {
    "click_three_candidates": PromptCase(
        step(
            click_step(
                "export",
                intent="Click the 'Export ledger' button",
                target=EXPORT.model_dump(mode="json"),
            )
        ),
        EXPORT,
        (
            shown(
                1,
                "c1",
                EXPORT,
                0.58,
                identity={"name": "Download ledger"},
                facts={"text": "Download ledger"},
            ),
            shown(
                2,
                "c2",
                EXPORT,
                0.47,
                identity={"name": "Export invoices"},
                facts={"text": "Export invoices", "nearby_text": ("Invoices",)},
            ),
            shown(
                3,
                "c4",
                EXPORT,
                0.41,
                identity={"tag": "a", "role": "link", "input_type": None, "name": "Ledger"},
                facts={"tag": "a", "type": None, "text": "Ledger", "nearby_text": ()},
            ),
        ),
    ),
    "fill_one_candidate": PromptCase(
        step(
            fill_step(
                "reference",
                intent="Fill the 'Customer reference' field",
                target=REFERENCE.model_dump(mode="json"),
                checkpoints=[{"kind": "field_has_value"}],
            )
        ),
        REFERENCE,
        (
            shown(
                1,
                "c1",
                REFERENCE,
                0.52,
                identity={"name": "Client reference"},
                facts={"label_text": "Client reference"},
            ),
        ),
    ),
    "role_less_password": PromptCase(
        step(
            fill_step(
                "passcode",
                intent="Fill the 'Vault passcode' field",
                target=PASSCODE.model_dump(mode="json"),
                value={"kind": "secret", "name": "vault_passcode"},
                checkpoints=[{"kind": "field_has_value"}],
            )
        ),
        PASSCODE,
        (
            shown(
                1,
                "c1",
                PASSCODE,
                0.55,
                identity={"name": "Passcode"},
                facts={"label_text": "Passcode"},
            ),
        ),
    ),
}


def render_case(
    case: PromptCase, scrubber: SecretScrubber | None = None
) -> tuple[ChatMessage, ...]:
    """The messages a case renders to."""
    return render_messages(case.step, case.fingerprint, case.shown, scrubber or SecretScrubber())


def golden_text(messages: tuple[ChatMessage, ...]) -> str:
    """Messages as a golden file holds them: each under a line naming its role."""
    return "".join(f"--- {message.role.value}\n{message.text}\n" for message in messages)
