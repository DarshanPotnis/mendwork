"""Static guards on the chaos portal's JavaScript.

Determinism depends on the portal never consulting the clock, the locale, or unseeded
randomness. The browser tests prove the result; these guards stop a regression before it
reaches a browser.

Matching is by code token, not substring: `formatDate` and `updatedAt` are fine, while
`new Date()` is not. Comments and string literals are scanned too, deliberately. A
comment-aware scanner would need a JavaScript tokenizer to get right, and would open a
loophole in exchange for nothing: a comment can always say "the clock" instead of naming
the API.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

PORTAL_ROOT: Final = Path(__file__).resolve().parents[2] / "chaos-portal"
MUTATIONS: Final = PORTAL_ROOT / "js" / "mutations"
CHAOS_BUTTON: Final = PORTAL_ROOT / "js" / "chaos" / "chaos-button.js"

_BEFORE: Final = r"(?<![\w$])"
_AFTER: Final = r"(?![\w$])"


@dataclass(frozen=True)
class BannedApi:
    """A browser API the portal must not call, matched as a whole JavaScript token."""

    name: str
    pattern: re.Pattern[str]


def _token(expression: str) -> re.Pattern[str]:
    return re.compile(f"{_BEFORE}{expression}{_AFTER}")


DATE: Final = BannedApi("Date", _token("Date"))
MATH_RANDOM: Final = BannedApi("Math.random", _token(r"Math\s*\.\s*random"))
PERFORMANCE_NOW: Final = BannedApi("performance.now", _token(r"performance\s*\.\s*now"))
CRYPTO: Final = BannedApi("crypto", _token("crypto"))
INTL: Final = BannedApi("Intl", _token("Intl"))
TO_LOCALE: Final = BannedApi("toLocale*", _token(r"toLocale\w*"))

MUTATION_RULES: Final = (DATE, MATH_RANDOM, PERFORMANCE_NOW, CRYPTO)
PORTAL_RULES: Final = (DATE, MATH_RANDOM, PERFORMANCE_NOW, CRYPTO, INTL, TO_LOCALE)


@dataclass(frozen=True)
class Finding:
    api: str
    line: int
    text: str


def scan(source: str, rules: tuple[BannedApi, ...]) -> list[Finding]:
    """Every use of a banned API in `source`, with its 1-based line number."""
    return [
        Finding(rule.name, number, line.strip())
        for number, line in enumerate(source.splitlines(), start=1)
        for rule in rules
        if rule.pattern.search(line)
    ]


@pytest.mark.parametrize(
    "source",
    [
        "const label = formatDate(order.placedOn);",
        "const updatedAt = 3;",
        "function isIsoDate(value) {}",
        "const mathematics = random();",
        "const cryptography = 'none';",
        "const $Date = 1; const Date$ = 2;",
        "performance_now();",
    ],
)
def test_ordinary_identifiers_do_not_trigger_the_guard(source: str) -> None:
    assert scan(source, PORTAL_RULES) == []


@pytest.mark.parametrize(
    ("source", "api"),
    [
        ("const now = new Date();", "Date"),
        ("const stamp = Date.now();", "Date"),
        ("const roll = Math.random();", "Math.random"),
        ("const roll = Math . random ();", "Math.random"),
        ("const t = performance.now();", "performance.now"),
        ("const bytes = crypto.getRandomValues(buffer);", "crypto"),
        ("const bytes = window.crypto.getRandomValues(buffer);", "crypto"),
        ("const money = new Intl.NumberFormat('en-US');", "Intl"),
        ("const text = total.toLocaleString();", "toLocale*"),
        ("const day = when.toLocaleDateString('en-GB');", "toLocale*"),
        ("// uses Date for the timestamp", "Date"),
    ],
)
def test_banned_apis_trigger_the_guard(source: str, api: str) -> None:
    assert [finding.api for finding in scan(source, PORTAL_RULES)] == [api]


def test_findings_report_the_line_they_are_on() -> None:
    source = "const a = 1;\nconst b = Math.random();\n"

    assert scan(source, MUTATION_RULES) == [Finding("Math.random", 2, "const b = Math.random();")]


def _javascript_files(root: Path) -> list[Path]:
    files = sorted(root.rglob("*.js"))
    assert files, f"no JavaScript found under {root}; the guard would pass vacuously"
    return files


def test_mutations_use_no_clock_timer_or_unseeded_randomness() -> None:
    files = _javascript_files(MUTATIONS)
    assert len(files) == 12

    findings = {path.name: scan(path.read_text(encoding="utf-8"), MUTATION_RULES) for path in files}

    assert {name: found for name, found in findings.items() if found} == {}


def test_the_portal_uses_no_clock_locale_or_randomness_outside_the_chaos_button() -> None:
    findings = {}
    for path in _javascript_files(PORTAL_ROOT):
        rules = tuple(
            rule for rule in PORTAL_RULES if not (path == CHAOS_BUTTON and rule is CRYPTO)
        )
        found = scan(path.read_text(encoding="utf-8"), rules)
        if found:
            findings[str(path.relative_to(PORTAL_ROOT))] = found

    assert findings == {}


def test_the_chaos_button_really_is_the_one_crypto_user() -> None:
    assert [f.api for f in scan(CHAOS_BUTTON.read_text(encoding="utf-8"), (CRYPTO,))] == ["crypto"]


def test_every_javascript_file_opts_into_type_checking() -> None:
    unchecked = [
        str(path.relative_to(PORTAL_ROOT))
        for path in _javascript_files(PORTAL_ROOT)
        if not path.read_text(encoding="utf-8").startswith("// @ts-check\n")
    ]

    assert unchecked == []
