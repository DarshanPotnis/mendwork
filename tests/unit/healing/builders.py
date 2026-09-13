"""Builders for heal tests: fingerprints and live candidates from made-up applications.

Nothing here comes from the chaos portal. A ledger's export button, an invoice's customer
reference field, a vault passcode: healing must work on pages it has never seen.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import TypeAdapter

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import CandidateOrigin, FeatureScores, SafetyRejection
from mendwork.engine.domain.steps import Step
from mendwork.engine.healing.scoring import ScoredElement
from mendwork.engine.ports.browser_types import ElementIdentity, ElementRef
from mendwork.engine.ports.candidate_types import LiveCandidate
from mendwork.engine.ports.element_types import Box, ElementFacts
from tests.fakes.browser import Effect, FakeBrowser, FakeElement
from tests.unit.replay.builders import selector

STEP: Final[TypeAdapter[Step]] = TypeAdapter(Step)
EXPORT_TEST_ID: Final = selector(strategy="test_id", value="ledger-export")
EXPORT_ROLE: Final = selector(strategy="role_name", role="button", name="Export ledger")
REFERENCE_LABEL: Final = selector(strategy="label", value="Customer reference")
_TEXT_TYPES: Final = frozenset({"text", "email", "search", "tel", "url", "password", "number"})


def export_button(**overrides: object) -> Fingerprint:
    """A ledger's export button, recorded with every clue."""
    values: dict[str, object] = {
        "tag": "button",
        "role": "button",
        "accessible_name": "Export ledger",
        "text": "Export ledger",
        "attributes": {"id": "export-ledger", "data_testid": "ledger-export", "type": "button"},
        "nearby_text": ["Quarterly ledger"],
        "structural_path": "main > article > div > button",
        "bbox": {"x": 0.6, "y": 0.3, "width": 0.12, "height": 0.05},
        "selectors": [EXPORT_TEST_ID.model_dump(), EXPORT_ROLE.model_dump()],
    }
    return Fingerprint.model_validate({**values, **overrides})


def reference_field(**overrides: object) -> Fingerprint:
    """An invoice form's customer reference field."""
    values: dict[str, object] = {
        "tag": "input",
        "role": "textbox",
        "accessible_name": "Customer reference",
        "label_text": "Customer reference",
        "attributes": {"id": "customer-reference", "name": "reference", "type": "text"},
        "nearby_text": ["Invoice details"],
        "structural_path": "main > form > div > input",
        "bbox": {"x": 0.2, "y": 0.4, "width": 0.3, "height": 0.05},
        "selectors": [REFERENCE_LABEL.model_dump()],
    }
    return Fingerprint.model_validate({**values, **overrides})


def identity_of(fingerprint: Fingerprint, *, confirmed: bool | None = None) -> ElementIdentity:
    """The identity the page would report for the recorded element."""
    return ElementIdentity(
        tag=fingerprint.tag,
        input_type=fingerprint.attributes.type,
        role=fingerprint.role.value if fingerprint.role is not None else None,
        name=fingerprint.accessible_name or "",
        confirmed=confirmed,
    )


def facts_of(fingerprint: Fingerprint) -> ElementFacts:
    """The facts the page would report for the recorded element, unchanged."""
    attributes = fingerprint.attributes
    bbox = fingerprint.bbox
    kind = (attributes.type or "text").lower()
    return ElementFacts(
        tag=fingerprint.tag,
        id=attributes.id,
        name=attributes.name,
        type=attributes.type,
        autocomplete=attributes.autocomplete,
        placeholder=attributes.placeholder,
        aria_label=attributes.aria_label,
        data_testid=attributes.data_testid,
        href=attributes.href,
        label_text=fingerprint.label_text,
        text=fingerprint.text,
        own_text=fingerprint.text,
        nearby_text=fingerprint.nearby_text,
        structural_path=fingerprint.structural_path,
        box=None if bbox is None else Box(x=bbox.x, y=bbox.y, width=bbox.width, height=bbox.height),
        text_entry=fingerprint.tag == "textarea"
        or (fingerprint.tag == "input" and kind in _TEXT_TYPES),
        masked=kind == "password",
    )


def live(
    fingerprint: Fingerprint,
    ref: str = "e1",
    *,
    identity: Mapping[str, object] | None = None,
    facts: Mapping[str, object] | None = None,
) -> LiveCandidate:
    """A live element like the recorded one, with any identity or facts changed."""
    return LiveCandidate(
        element=ElementRef(ref),
        identity=identity_of(fingerprint).model_copy(update=identity or {}),
        facts=facts_of(fingerprint).model_copy(update=facts or {}),
    )


def scored(
    score: float,
    key: str = "a",
    *,
    rejection: SafetyRejection | None = None,
    fingerprint: Fingerprint | None = None,
) -> ScoredElement:
    """A scored element with a chosen score, for acceptance tests."""
    zero = FeatureScores(
        name=0,
        label=0,
        attributes=0,
        role=0,
        tag_type=0,
        nearby_text=0,
        structural_path=0,
        position=0,
    )
    return ScoredElement(
        candidate=live(fingerprint or export_button(), key),
        origin=CandidateOrigin.PAGE,
        features=zero,
        score=score,
        signature=(key,),
        rejection=rejection,
    )


def add_element(
    page: FakeBrowser,
    key: str,
    fingerprint: Fingerprint,
    *,
    identity: Mapping[str, object] | None = None,
    facts: Mapping[str, object] | None = None,
    candidate: bool = True,
    confirmed: bool | None = True,
    enabled: bool = True,
    on_action: Effect | None = None,
) -> FakeElement:
    """Put an element like the recorded one on a scripted page, and in its candidate scan."""
    found = live(fingerprint, identity=identity, facts=facts)
    element = FakeElement(
        tag=found.identity.tag,
        name=found.identity.name,
        role=found.identity.role,
        input_type=found.identity.input_type,
        confirmed=confirmed,
        enabled=enabled,
        on_action=on_action,
    )
    page.elements[key] = element
    page.facts[key] = found.facts
    if candidate:
        page.candidates.append(key)
    return element


def step(document: dict[str, object]) -> Step:
    """A step from a document, validated as a workflow file's step would be."""
    return STEP.validate_python(document)
