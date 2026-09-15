"""An approval matches what an element is, never where it sits (ADR 0011)."""

from collections.abc import Mapping

import pytest
from pydantic import SecretStr

from mendwork.engine.healing.candidates import (
    SignatureMatch,
    approved_identity,
    candidate_signature,
    identity_signature,
)
from mendwork.engine.ports.element_types import Box
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.unit.healing.builders import export_button, live

PUSHED_DOWN = Box(x=0.6, y=0.42, width=0.12, height=0.05)
"""Where the recorded button sits once a cookie banner above it takes 12% of the page."""


def test_an_element_pushed_down_by_a_cookie_banner_keeps_its_approved_identity() -> None:
    scrubber = SecretScrubber()
    before = candidate_signature(live(export_button()))
    after = candidate_signature(live(export_button(), facts={"box": PUSHED_DOWN}))

    assert before != after
    assert identity_signature(before) == identity_signature(after)
    assert approved_identity(after, scrubber) == approved_identity(before, scrubber)
    assert SignatureMatch(approved_identity(before, scrubber), by_identity=scrubber).matches(after)
    assert not SignatureMatch(before).matches(after)
    assert SignatureMatch(before).matches(before)


@pytest.mark.parametrize(
    ("identity", "facts"),
    [
        ({"name": "Share ledger"}, {}),
        ({"role": "link"}, {}),
        ({}, {"id": "share-ledger"}),
        ({}, {"data_testid": "ledger-share"}),
        ({}, {"href": "/ledger/share"}),
        ({}, {"structural_path": "main > aside > button"}),
        ({}, {"nearby_text": ("Annual ledger",)}),
    ],
)
def test_anything_else_about_the_element_changes_its_approved_identity(
    identity: Mapping[str, object], facts: Mapping[str, object]
) -> None:
    scrubber = SecretScrubber()
    approved = approved_identity(candidate_signature(live(export_button())), scrubber)
    changed = candidate_signature(live(export_button(), identity=identity, facts=facts))

    assert not SignatureMatch(approved, by_identity=scrubber).matches(changed)


def test_a_secret_shown_near_the_element_never_reaches_its_approved_identity() -> None:
    scrubber = SecretScrubber()
    scrubber.register(SecretStr("hunter2-ledger"))
    signature = candidate_signature(
        live(export_button(), facts={"nearby_text": ("Signed in as hunter2-ledger",)})
    )

    approved = approved_identity(signature, scrubber)

    assert "hunter2-ledger" not in " ".join(approved)
    assert SignatureMatch(approved, by_identity=scrubber).matches(signature)
