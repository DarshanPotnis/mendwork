"""The words of a version diff and a heal's reason, pinned exactly (ADR 0013)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import pytest
from pydantic import TypeAdapter

from mendwork.engine.domain.checkpoints import Checkpoint
from mendwork.engine.domain.enums import CheckpointKind, VerificationStrength
from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.lineage import edit_version, heal_version
from mendwork.engine.domain.selectors import Selector
from mendwork.engine.domain.steps import Step
from mendwork.engine.domain.values import ValueRef
from mendwork.engine.patching.diff import FieldChange, SelectorLine, diff_versions, target_diff
from mendwork.engine.patching.words import (
    change_summary,
    checkpoint_words,
    element_kind,
    heal_reason,
    moment,
    score_words,
    selector_words,
    strength_adverb,
    strength_sentence,
    target_words,
    value_words,
)
from tests.fakes.clock import FakeClock
from tests.heal_changes import APPROVAL, heal_change
from tests.unit.patching.builders import with_intent
from tests.workflows import CREATED_AT_DATETIME, version

SELECTOR: Final[TypeAdapter[Selector]] = TypeAdapter(Selector)
CHECKPOINT: Final[TypeAdapter[Checkpoint]] = TypeAdapter(Checkpoint)
VALUE: Final[TypeAdapter[ValueRef]] = TypeAdapter(ValueRef)
CLOCK: Final = FakeClock(CREATED_AT_DATETIME)
DOWNLOAD_BUTTON: Final = Fingerprint.model_validate(
    {
        "tag": "button",
        "role": "button",
        "accessible_name": "Download CSV",
        "text": "Download CSV",
        "attributes": {"id": "download-csv", "type": "button", "data_testid": "download-csv"},
        "nearby_text": ["Date range"],
        "structural_path": "main > section > form > div > button",
        "selectors": [
            {"strategy": "test_id", "value": "download-csv"},
            {"strategy": "role_name", "role": "button", "name": "Download CSV"},
            {"strategy": "text", "value": "Download CSV"},
            {"strategy": "css", "value": "#download-csv"},
        ],
    }
)
DOWNLOAD_LINK: Final = Fingerprint.model_validate(
    {
        "tag": "a",
        "role": "link",
        "accessible_name": "Download CSV",
        "text": "Download CSV",
        "attributes": {
            "id": "download-csv",
            "data_testid": "download-csv",
            "href": "/reports.html",
        },
        "nearby_text": ["Date range"],
        "structural_path": "main > section > form > div > a",
        "selectors": [
            {"strategy": "test_id", "value": "download-csv"},
            {"strategy": "role_name", "role": "link", "name": "Download CSV"},
            {"strategy": "text", "value": "Download CSV"},
            {"strategy": "css", "value": "#download-csv"},
        ],
    }
)


def test_a_button_that_became_a_link_reads_as_the_plan_promised() -> None:
    diff = target_diff(DOWNLOAD_BUTTON, DOWNLOAD_LINK)

    assert list(diff.fields) == [
        FieldChange("Kind", "a button", "a link", changed=True),
        FieldChange("Name", '"Download CSV"', '"Download CSV"', changed=False),
        FieldChange("Text", '"Download CSV"', '"Download CSV"', changed=False),
        FieldChange("Test id", '"download-csv"', '"download-csv"', changed=False),
        FieldChange("Id on the page", '"download-csv"', '"download-csv"', changed=False),
        FieldChange("Link address", None, '"/reports.html"', changed=True),
        FieldChange("Button type", '"button"', None, changed=True),
        FieldChange("Nearby text", '"Date range"', '"Date range"', changed=False),
        FieldChange(
            "Place in the page",
            "main > section > form > div > button".replace(" > ", f" {chr(0x203A)} "),
            "main > section > form > div > a".replace(" > ", f" {chr(0x203A)} "),
            changed=True,
        ),
    ]
    assert diff.selectors == (
        SelectorLine('by its test id "download-csv"', 'by its test id "download-csv"'),
        SelectorLine('as a button named "Download CSV"', 'as a link named "Download CSV"'),
        SelectorLine('by its text "Download CSV"', 'by its text "Download CSV"'),
        SelectorLine('by its id "#download-csv"', 'by its id "#download-csv"'),
    )


def test_selectors_added_and_removed_are_paired_by_strategy() -> None:
    old = DOWNLOAD_BUTTON
    new = DOWNLOAD_LINK.model_copy(
        update={
            "selectors": (
                DOWNLOAD_LINK.selectors[1],
                SELECTOR.validate_python({"strategy": "label", "value": "CSV"}),
            )
        }
    )

    assert target_diff(old, new).selectors == (
        SelectorLine('as a button named "Download CSV"', 'as a link named "Download CSV"'),
        SelectorLine(None, 'by its label "CSV"'),
        SelectorLine('by its test id "download-csv"', None),
        SelectorLine('by its text "Download CSV"', None),
        SelectorLine('by its id "#download-csv"', None),
    )


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        ({"strategy": "test_id", "value": "save"}, 'by its test id "save"'),
        (
            {"strategy": "role_name", "role": "row", "name": "INV-2231", "exact": False},
            'as a row whose name contains "INV-2231"',
        ),
        ({"strategy": "label", "value": "Email"}, 'by its label "Email"'),
        (
            {"strategy": "placeholder", "value": "Search", "exact": False},
            'by its placeholder "Search" (containing it)',
        ),
        ({"strategy": "text", "value": "Open"}, 'by its text "Open"'),
        ({"strategy": "css", "value": "form button"}, 'by the CSS selector "form button"'),
        (
            {
                "strategy": "role_name",
                "role": "button",
                "name": "Open",
                "within": {"strategy": "test_id", "value": "orders"},
            },
            'as a button named "Open", inside the element found by its test id "orders"',
        ),
    ],
)
def test_every_selector_strategy_reads_as_how_the_element_is_found(
    raw: dict[str, object], words: str
) -> None:
    assert selector_words(SELECTOR.validate_python(raw)) == words


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        (
            {"kind": "url_matches", "mode": "regex", "pattern": ".*/reports\\.html"},
            'the browser went to an address matching ".*/reports\\.html"',
        ),
        (
            {"kind": "url_matches", "mode": "exact", "pattern": "https://a.test/"},
            'the browser went to exactly "https://a.test/"',
        ),
        (
            {"kind": "url_matches", "mode": "prefix", "pattern": "https://a.test/"},
            'the browser went to an address starting with "https://a.test/"',
        ),
        (
            {"kind": "element_visible", "selector": {"strategy": "test_id", "value": "done"}},
            'an element appeared, found by its test id "done"',
        ),
        ({"kind": "text_present", "text": "Saved"}, 'the text "Saved" appeared'),
        ({"kind": "download_completed", "filename_pattern": ".*"}, "the file download finished"),
        (
            {
                "kind": "response_received",
                "mode": "prefix",
                "pattern": "https://a.test/api",
                "status_min": 200,
                "status_max": 299,
            },
            'a response from an address matching "https://a.test/api" arrived with status 200 to '
            "299",
        ),
        ({"kind": "no_error_banner"}, "no error message was shown"),
        ({"kind": "field_has_value"}, "the field held the typed value"),
    ],
)
def test_every_checkpoint_reads_as_what_it_looks_for(raw: dict[str, object], words: str) -> None:
    assert checkpoint_words(CHECKPOINT.validate_python(raw)) == words


@pytest.mark.parametrize(
    ("role", "tag", "input_type", "words"),
    [
        ("button", "button", None, "a button"),
        ("textbox", "input", "email", "an email field"),
        (None, "input", "date", "a date field"),
        (None, "input", None, "a field"),
        ("menuitem", "li", None, "a menu item"),
        ("grid", "div", None, "a grid"),
        (None, "custom-widget", None, "a <custom-widget> element"),
    ],
)
def test_elements_are_named_by_what_they_are(
    role: str | None, tag: str, input_type: str | None, words: str
) -> None:
    assert element_kind(role, tag, input_type) == words


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        ({"kind": "literal", "value": "Ada Lovelace"}, "a value written in the workflow"),
        ({"kind": "input", "name": "account_email"}, 'the run input "account_email"'),
        ({"kind": "secret", "name": "portal_password"}, 'the secret "portal_password"'),
    ],
)
def test_values_are_described_and_never_quoted(raw: dict[str, object], words: str) -> None:
    assert value_words(VALUE.validate_python(raw)) == words
    assert value_words(None) is None


def test_strength_reads_as_how_much_the_check_proves() -> None:
    assert strength_sentence(VerificationStrength.STRONG, [CheckpointKind.TEXT_PRESENT]) == (
        "That check is strong: it shows the right element was used."
    )
    assert strength_sentence(VerificationStrength.WEAK, [CheckpointKind.URL_MATCHES]) == (
        "That check is weak: another link to the same page would pass it too."
    )
    assert strength_sentence(VerificationStrength.WEAK, [CheckpointKind.FIELD_HAS_VALUE]) == (
        "That check is weak: it shows a value landed in a field, not that it was the right field."
    )
    assert strength_sentence(VerificationStrength.NONE, []) == (
        "No check shows which element was used."
    )
    assert [strength_adverb(item) for item in VerificationStrength] == [
        "strongly",
        "weakly",
        "without a check that proves it",
    ]


def test_a_rung_2_heal_explains_itself_in_the_plan_s_words() -> None:
    first = version(
        steps=[
            {
                "id": "download_csv",
                "intent": "Click the 'Download CSV' button",
                "action": "click",
                "risk": "safe",
                "target": DOWNLOAD_BUTTON.model_dump(mode="json"),
                "checkpoints": [{"kind": "download_completed", "filename_pattern": "report\\.csv"}],
            }
        ]
    )
    change = heal_change(
        first,
        "download_csv",
        new_target=DOWNLOAD_LINK,
        score=0.845007916,
        margin=0.628341249,
        checkpoints=["download_completed"],
    )

    assert heal_reason(change, first.steps[0]) == (
        "Repaired automatically by similarity scoring (rung 2, no AI model) and confirmed by the "
        "step's check: the file download finished. That check is strong: it shows the right "
        "element was used.",
        "Similarity 0.85 (0.60 needed), 0.63 ahead of the next closest element (0.15 needed).",
    )


def test_a_weak_model_heal_names_its_model_cost_and_approval() -> None:
    first = version()
    change = heal_change(
        first,
        rung=3,
        score=None,
        margin=None,
        threshold=None,
        required_margin=None,
        strength="weak",
        checkpoints=["url_matches", "no_error_banner"],
        approval=APPROVAL,
        model={
            "provider": "ollama",
            "model": "qwen3",
            "prompt_version": "choose-candidate/1",
            "calls": 1,
            "input_tokens": 400,
            "output_tokens": 58,
            "estimated_cost_usd": str(Decimal(0)),
            "unpriced_calls": 0,
        },
    )

    assert heal_reason(change, None) == (
        "Repaired by an AI model (rung 3), which chose it from the closest elements on the page "
        "and confirmed by the step's check: the browser reached the recorded address. That check "
        "is weak: another link to the same page would pass it too.",
        'Model: ollama "qwen3", 1 call, 458 tokens, $0.0000.',
        "Approved by a person (proposal save-1, audit entry 3) before it acted.",
    )


@pytest.mark.parametrize(
    ("counts", "words"),
    [
        ({"calls": 1, "input_tokens": 400, "output_tokens": 58}, "1 call, 458 tokens, $0.0000."),
        (
            {"calls": 1, "unreported_token_calls": 1},
            "1 call, token counts not reported by this provider, $0.0000.",
        ),
        (
            {"calls": 3, "input_tokens": 400, "output_tokens": 58, "unreported_token_calls": 1},
            "3 calls, 458 tokens for 2 of 3 calls (token counts not reported for 1), $0.0000.",
        ),
        (
            {"calls": 2, "input_tokens": 400, "output_tokens": 58, "estimated_cost_usd": "0.0123"},
            "2 calls, 458 tokens, $0.0123.",
        ),
        (
            {"calls": 2, "input_tokens": 400, "output_tokens": 58, "unpriced_calls": 2},
            "2 calls, 458 tokens, cost unknown (no price in MENDWORK_MODEL_PRICES).",
        ),
        (
            {
                "calls": 3,
                "input_tokens": 400,
                "output_tokens": 58,
                "estimated_cost_usd": "0.0123",
                "unpriced_calls": 1,
            },
            "3 calls, 458 tokens, $0.0123 for 2 of 3 calls (cost unknown for 1, no price in "
            "MENDWORK_MODEL_PRICES).",
        ),
    ],
    ids=[
        "tokens reported",
        "tokens not reported",
        "tokens reported for some calls",
        "all priced",
        "none priced",
        "some priced",
    ],
)
def test_a_model_heal_never_shows_unreported_tokens_as_0_or_unpriced_calls_as_free(
    counts: dict[str, int | str], words: str
) -> None:
    change = heal_change(
        version(),
        rung=3,
        score=None,
        margin=None,
        threshold=None,
        required_margin=None,
        model={
            "provider": "fake",
            "model": "scripted",
            "prompt_version": "choose-candidate/1",
            "estimated_cost_usd": "0",
            **counts,
        },
    )

    assert heal_reason(change, None)[1] == f'Model: fake "scripted", {words}'


def test_every_change_kind_has_a_history_line() -> None:
    first = version()
    edited = edit_version(
        first, with_intent(first, "save", "Save"), summary="tightened", clock=CLOCK
    )
    healed = heal_version(first, heal_change(first), clock=CLOCK)

    assert change_summary(first.change, first.steps) == "First version"
    assert change_summary(edited.change, edited.steps) == "Edited by hand: tightened"
    assert change_summary(healed.change, healed.steps) == (
        "Step 3 save healed at rung 2, verified strongly"
    )
    assert score_words(None, None, None, None) is None
    assert score_words(0.9, None, None, None) == "Similarity 0.90."
    assert moment(datetime(2026, 9, 5, 8, 7, tzinfo=UTC)) == "5 Sep 2026 08:07 UTC"
    assert target_words(DOWNLOAD_LINK) == 'a link named "Download CSV"'


def test_a_version_diff_lists_only_changed_steps_and_the_versions_between() -> None:
    first = version()
    second = heal_version(first, heal_change(first), clock=CLOCK)
    third = edit_version(
        second, with_intent(second, "fill_name", "Type the full name"), summary="e", clock=CLOCK
    )
    history = (first, second, third)

    diff = diff_versions(first, third, history)

    assert [(step.index, step.step_id) for step in diff.steps] == [(1, "fill_name"), (2, "save")]
    assert diff.unchanged_steps == 1
    assert [item.version for item in diff.between] == [2, 3]
    [intent] = diff.steps[0].fields
    assert intent == FieldChange(
        "Intent", "\"Fill the 'Full name' field\"", '"Type the full name"', changed=True
    )
    assert diff.steps[1].target is not None
    assert diff_versions(third, first, history).between == ()
    assert diff_versions(first, first).steps == ()


def test_checkpoint_value_risk_key_and_declaration_changes_are_listed() -> None:
    first = version(
        inputs=[{"name": "who", "kind": "text", "required": True}],
        steps=[
            {
                "id": "open_portal",
                "intent": "Open",
                "action": "navigate",
                "risk": "safe",
                "value": {"kind": "literal", "value": "https://portal.example.test/"},
            },
            {
                "id": "fill_name",
                "intent": "Fill",
                "action": "fill",
                "risk": "caution",
                "target": {
                    "tag": "input",
                    "role": "textbox",
                    "accessible_name": "Full name",
                    "structural_path": "main > input",
                    "selectors": [{"strategy": "label", "value": "Full name"}],
                },
                "value": {"kind": "input", "name": "who"},
                "checkpoints": [{"kind": "field_has_value"}],
            },
            {
                "id": "submit",
                "intent": "Press",
                "action": "press",
                "risk": "caution",
                "key": "Enter",
            },
        ],
    )
    steps = [step.model_dump(mode="json") for step in first.steps]
    steps[1]["value"] = {"kind": "literal", "value": "Grace"}
    steps[1]["checkpoints"] = [{"kind": "text_present", "text": "Hello"}]
    steps[2]["key"] = "Tab"
    steps[2]["risk"] = "irreversible"
    second_steps: tuple[Step, ...] = tuple(
        TypeAdapter(Step).validate_python(item) for item in steps
    )
    second = first.model_copy(update={"inputs": (), "steps": second_steps})

    diff = diff_versions(first, second)

    fill, press = diff.steps
    assert fill.fields == (
        FieldChange(
            "Value", 'the run input "who"', "a value written in the workflow", changed=True
        ),
    )
    assert (fill.checkpoints_added, fill.checkpoints_removed) == (
        ('the text "Hello" appeared',),
        ("the field held the typed value",),
    )
    assert press.fields == (
        FieldChange("Risk", "caution", "irreversible", changed=True),
        FieldChange("Key", "Enter", "Tab", changed=True),
    )
    assert diff.declarations == (FieldChange("Inputs", "who", None, changed=True),)
