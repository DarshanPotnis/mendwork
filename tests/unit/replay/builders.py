"""Builders for engine replay tests: selectors, fingerprints, fake elements, and config."""

from pydantic import TypeAdapter

from mendwork.engine.domain.enums import AriaRole
from mendwork.engine.domain.fingerprint import Fingerprint, FingerprintAttributes
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import MendworkError
from mendwork.engine.healing.config import FeatureWeights, HealingConfig
from mendwork.engine.replay.config import ReplayConfig, RetryPolicy
from tests.fakes.browser import Effect, FakeBrowser, FakeElement
from tests.fakes.timer import FakeTimer
from tests.unit.recording.builders import VOCABULARY

SELECTOR: TypeAdapter[Selector] = TypeAdapter(Selector)

WEIGHTS = FeatureWeights(
    name=0.25,
    label=0.05,
    attributes=0.25,
    role=0.10,
    tag_type=0.05,
    nearby_text=0.10,
    structural_path=0.10,
    position=0.10,
)


def healing(**overrides: object) -> HealingConfig:
    """The heal ladder's configuration, with Settings' defaults unless overridden."""
    values: dict[str, object] = {
        "weights": WEIGHTS,
        "accept_threshold": 0.60,
        "accept_margin": 0.15,
        "name_similarity_floor": 0.5,
        "position_scale": 0.25,
        "candidates_max": 4_000,
        "max_attempts": 2,
        "authentication_max_attempts": 1,
        "report_candidates": 5,
        "timeout_ms": 30_000,
        "vocabulary": VOCABULARY,
    }
    return HealingConfig.model_validate({**values, **overrides})


def selector(**raw: object) -> Selector:
    return SELECTOR.validate_python(raw)


TEST_ID = selector(strategy="test_id", value="download-csv")
ROLE_NAME = selector(strategy="role_name", role="button", name="Download CSV")
CSS = selector(strategy="css", value="#download-csv")


def button_fingerprint(
    name: str = "Download CSV", selectors: tuple[Selector, ...] = (TEST_ID, ROLE_NAME, CSS)
) -> Fingerprint:
    return Fingerprint(
        tag="button",
        role=AriaRole.BUTTON,
        accessible_name=name,
        structural_path="main > form > button",
        selectors=selectors,
    )


def password_fingerprint(selectors: tuple[Selector, ...]) -> Fingerprint:
    return Fingerprint(
        tag="input",
        accessible_name="Password",
        attributes=FingerprintAttributes(type="password"),
        structural_path="main > form > input",
        selectors=selectors,
    )


def button(
    name: str = "Download CSV",
    *,
    attached: bool = True,
    enabled: bool = True,
    confirmed: bool | None = True,
    on_action: Effect | None = None,
    action_error: MendworkError | None = None,
) -> FakeElement:
    return FakeElement(
        tag="button",
        role="button",
        name=name,
        attached=attached,
        enabled=enabled,
        confirmed=confirmed,
        on_action=on_action,
        action_error=action_error,
    )


def browser(
    *,
    elements: dict[str, FakeElement] | None = None,
    finds: dict[Selector, str | tuple[int, ...]] | None = None,
    url: str = "https://portal.example.test/",
    text: str = "",
    alerts: tuple[str, ...] = (),
    quiet: bool = True,
    unstable_reads: int = 0,
) -> FakeBrowser:
    return FakeBrowser(
        timer=FakeTimer(),
        elements=elements or {},
        finds=finds or {},
        url=url,
        text=text,
        alerts=alerts,
        quiet=quiet,
        unstable_reads=unstable_reads,
    )


def config(**overrides: object) -> ReplayConfig:
    values: dict[str, object] = {
        "step_timeout_ms": 2_000,
        "checkpoint_timeout_ms": 1_000,
        "navigation_timeout_ms": 3_000,
        "run_timeout_ms": 60_000,
        "settle_timeout_ms": 100,
        "settle_quiet_frames": 2,
        "retry": RetryPolicy(
            max_attempts=3,
            initial_delay_ms=500,
            max_delay_ms=4_000,
            multiplier=2.0,
            jitter_ratio=0.5,
        ),
        "healing": healing(),
    }
    return ReplayConfig.model_validate({**values, **overrides})
