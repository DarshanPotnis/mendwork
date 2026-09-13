"""Builders for recorder tests: facts, identities, captures, configuration, and fake pages."""

from typing import Final

import structlog
from pydantic import TypeAdapter

from mendwork.engine.domain.selectors import Selector
from mendwork.engine.ports.browser_types import ElementIdentity
from mendwork.engine.ports.element_types import ElementFacts
from mendwork.engine.ports.recording_types import CaptureRef, Landmark
from mendwork.engine.recording.config import RecordingConfig
from mendwork.engine.recording.context import CaptureContext
from mendwork.engine.replay.config import RetryPolicy
from mendwork.engine.safety.risk import RiskVocabulary
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import (
    DEFAULT_DANGER_WORDS,
    DEFAULT_READ_WORDS,
    DEFAULT_SESSION_PHRASES,
    DEFAULT_SOFT_VERBS,
    DEFAULT_VIEW_STATE_NOUNS,
)
from tests.fakes.browser import FakeElement
from tests.fakes.ports import SequenceRandom
from tests.fakes.recording import FakeRecordingBrowser
from tests.fakes.timer import FakeTimer

DOC: Final = "a" * 32
OTHER_DOC: Final = "b" * 32
START_URL: Final = "https://portal.example.test/index.html"
SELECTOR: Final[TypeAdapter[Selector]] = TypeAdapter(Selector)

VOCABULARY: Final = RiskVocabulary(
    danger_words=DEFAULT_DANGER_WORDS,
    soft_verbs=DEFAULT_SOFT_VERBS,
    view_state_nouns=DEFAULT_VIEW_STATE_NOUNS,
    read_words=DEFAULT_READ_WORDS,
    session_phrases=DEFAULT_SESSION_PHRASES,
)
CONFIG: Final = RecordingConfig(
    step_timeout_ms=1_000,
    settle_timeout_ms=100,
    settle_quiet_frames=2,
    navigation_timeout_ms=1_000,
    checkpoint_timeout_ms=500,
    scope_ancestors_max=6,
    landmarks_max=40,
    retry=RetryPolicy(
        max_attempts=1, initial_delay_ms=0, max_delay_ms=0, multiplier=1.0, jitter_ratio=0.0
    ),
    risk=VOCABULARY,
)


def selector(**raw: object) -> Selector:
    return SELECTOR.validate_python(raw)


def by_test_id(value: str) -> Selector:
    return selector(strategy="test_id", value=value)


def role(role_name: str, name: str, **extra: object) -> Selector:
    return selector(strategy="role_name", role=role_name, name=name, **extra)


def text(value: str) -> Selector:
    return selector(strategy="text", value=value)


def css(value: str) -> Selector:
    return selector(strategy="css", value=value)


def facts(tag: str = "button", /, **overrides: object) -> ElementFacts:
    return ElementFacts.model_validate(
        {"tag": tag, "structural_path": f"main > {tag}", **overrides}
    )


def identity(
    role_name: str | None = "button",
    name: str = "Save",
    *,
    tag: str = "button",
    confirmed: bool | None = True,
    input_type: str | None = None,
) -> ElementIdentity:
    return ElementIdentity(
        tag=tag, input_type=input_type, role=role_name, name=name, confirmed=confirmed
    )


def ref(sequence: int = 1, element: int | None = 1, document: str = DOC) -> CaptureRef:
    return CaptureRef(document=document, sequence=sequence, element=element)


def heading(name: str) -> Landmark:
    return Landmark(role="heading", name=name)


def browser(**fields: object) -> FakeRecordingBrowser:
    page = FakeRecordingBrowser(timer=FakeTimer(), **fields)  # type: ignore[arg-type] # test data
    page.url = START_URL
    return page


def context(page: FakeRecordingBrowser, config: RecordingConfig = CONFIG) -> CaptureContext:
    return CaptureContext(
        browser=page,
        config=config,
        timer=page.timer,
        randomness=SequenceRandom(),
        scrubber=SecretScrubber(),
        log=structlog.stdlib.get_logger("tests.recording"),
    )


def add_button(
    page: FakeRecordingBrowser,
    key: str = "save",
    *,
    name: str = "Save",
    element: int = 1,
    document: str = DOC,
    **fact_overrides: object,
) -> FakeElement:
    """A button the page captured, found by its test id, role and name, and text."""
    button = FakeElement(tag="button", role="button", name=name)
    page.elements[key] = button
    page.facts[key] = facts(data_testid=key, text=name, own_text=name, **fact_overrides)
    page.captured[(document, element)] = key
    page.finds[by_test_id(key)] = key
    page.finds[role("button", name)] = key
    page.finds[text(name)] = key
    return button


def add_heading(page: FakeRecordingBrowser, name: str, key: str | None = None) -> None:
    """A visible heading an element_visible checkpoint can find exactly once."""
    element_key = key or f"heading-{name}"
    page.elements[element_key] = FakeElement(tag="h1", role="heading", name=name)
    page.finds[role("heading", name)] = element_key
    page.landmarks = (*page.landmarks, heading(name))
