"""Pure text rules for checkpoints."""

from collections.abc import Iterable


def normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace to one space and trim the ends."""
    return " ".join(text.split())


def contains_text(page_text: str, expected: str) -> bool:
    """Whether rendered page text contains the expected text, whitespace-normalized and
    case-sensitive, as text_present promises."""
    return normalize_whitespace(expected) in normalize_whitespace(page_text)


def first_error_banner(alert_texts: Iterable[str]) -> str | None:
    """The first alert that says something; an empty or whitespace-only alert is no banner."""
    return next((normalize_whitespace(text) for text in alert_texts if text.strip()), None)
