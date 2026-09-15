"""What deriving a target's selectors and fingerprint needs, whether a recording or a heal asks.

The recorder fingerprints the element a person used; a verified heal fingerprints the element the
ladder found, so the next version targets it (ADR 0013). Both go through one ``TargetRecorder`` on
this context, so there is exactly one selector derivation.
"""

from dataclasses import dataclass

from mendwork.engine.ports.browser import BrowserPort
from mendwork.engine.ports.timer import Timer
from mendwork.engine.safety.secret_scrub import SecretScrubber


@dataclass(frozen=True, slots=True)
class TargetCaptureContext:
    """The browser, time, and tuning a target capture reads with."""

    browser: BrowserPort
    timer: Timer
    scrubber: SecretScrubber
    step_timeout_ms: int
    """The longest a capture may take, unless the caller's deadline is sooner."""
    settle_timeout_ms: int
    settle_quiet_frames: int
    scope_ancestors_max: int
    """How many ancestors an ambiguous selector is tried inside."""
