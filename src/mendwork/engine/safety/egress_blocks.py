"""Egress refusals as evidence: where each was enforced, and the step failure they add up to.

The same policy is enforced in three places (ADR 0011): the engine checks a URL before asking the
browser to load it, the browser adapter's document filter checks every document request (redirect
hops included), and its gateway checks every connection. Each refusal is kept as an EgressBlock,
and any block during a step fails the step with EgressBlocked: the run was pointed somewhere it
must not go, which is never retried or healed.
"""

from collections.abc import Sequence
from enum import StrEnum

from mendwork.engine.domain.base import DomainModel
from mendwork.engine.errors import EgressBlocked
from mendwork.engine.safety.egress import EgressRefusal


class EgressLayer(StrEnum):
    """Where a refusal was enforced."""

    NAVIGATION = "navigation"
    """The engine's check before asking the browser to load a URL."""
    DOCUMENT = "document"
    """The browser's document filter, on a document request or one of its redirect hops."""
    CONNECTION = "connection"
    """The browser's gateway, when it was asked to connect."""


class EgressBlock(DomainModel):
    """One refusal, and where it was enforced."""

    layer: EgressLayer
    main_frame: bool | None = None
    """For a document request, whether it was the page's own; None for a connection."""
    refusal: EgressRefusal


def egress_blocked(blocks: Sequence[EgressBlock]) -> EgressBlocked:
    """The step failure for one or more refusals, led by the first."""
    if not blocks:
        raise ValueError("an egress failure needs at least one refusal")
    first = blocks[0]
    refusal = first.refusal
    subject = refusal.host or "a URL"
    return EgressBlocked(
        f"the egress policy refused {subject}: {refusal.detail}",
        reason="egress_blocked",
        rule=refusal.rule.value,
        host=refusal.host,
        port=refusal.port,
        address=refusal.address,
        address_range=None if refusal.address_range is None else refusal.address_range.value,
        layer=first.layer.value,
        blocks=[block.model_dump(mode="json") for block in blocks],
    )
