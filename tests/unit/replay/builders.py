"""Builders for engine replay tests: selectors, fingerprints, fake elements, and config."""

from pydantic import TypeAdapter

from mendwork.engine.domain.enums import AriaRole
from mendwork.engine.domain.fingerprint import Fingerprint, FingerprintAttributes
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.errors import MendworkError
from mendwork.engine.replay.config import ReplayConfig, RetryPolicy
from tests.fakes.browser import Effect, FakeBrowser, FakeElement
from tests.fakes.timer import FakeTimer

SELECTOR: TypeAdapter[Selector] = TypeAdapter(Selector)


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
    }
    return ReplayConfig.model_validate({**values, **overrides})
