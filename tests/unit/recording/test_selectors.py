"""Selector candidates and scopes: ranked, deduplicated, and never invalid."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mendwork.engine.domain.enums import SelectorStrategy
from mendwork.engine.ports.recording_types import AncestorFacts
from mendwork.engine.recording.selectors import (
    candidate_selectors,
    css_selector,
    scope_selectors,
    summarize,
    with_scope,
)
from tests.unit.recording.builders import by_test_id, css, facts, identity, role, selector, text

RANK = [strategy.value for strategy in SelectorStrategy]


def test_a_button_gets_every_candidate_in_rank_order() -> None:
    found = candidate_selectors(
        facts(data_testid="view", text="View order PO-1042", own_text="View", id="view-po-1042"),
        identity("button", "View order PO-1042"),
    )

    assert found == (
        by_test_id("view"),
        role("button", "View order PO-1042"),
        role("button", "View", exact=False),
        text("View order PO-1042"),
        css("#view-po-1042"),
    )


def test_own_text_equal_to_the_name_adds_nothing() -> None:
    found = candidate_selectors(facts(own_text="save "), identity("button", "Save"))

    assert found == (role("button", "Save"),)


def test_an_unconfirmed_identity_gives_no_role_candidates() -> None:
    found = candidate_selectors(
        facts(text="Save", own_text="Save"), identity("button", "Save", confirmed=False)
    )

    assert found == (text("Save"),)


def test_an_unknown_role_gives_no_role_candidates() -> None:
    assert candidate_selectors(facts(), identity("not-a-role", "Save")) == ()


def test_fields_get_label_and_placeholder_but_never_text() -> None:
    found = candidate_selectors(
        facts(
            "input",
            label_text=" Email  address ",
            placeholder="you@example.test",
            text="ignored",
            text_entry=True,
            name="email",
        ),
        identity("textbox", "Email address", tag="input"),
    )

    assert found == (
        role("textbox", "Email address"),
        selector(strategy="label", value="Email address"),
        selector(strategy="placeholder", value="you@example.test"),
        css('input[name="email"]'),
    )


def test_long_or_invalid_text_is_not_a_candidate() -> None:
    assert candidate_selectors(facts(text="x" * 129), identity(None, "")) == ()
    assert candidate_selectors(facts(data_testid="bad" + chr(0) + "id"), identity(None, "")) == ()


@pytest.mark.parametrize(
    ("tag", "element_id", "name", "expected"),
    [
        ("button", "sign-in", None, "#sign-in"),
        ("button", ":r1:", None, None),
        ("button", "ember12345", None, None),
        ("div", "a1b2c3d4e5f6", None, None),
        ("button", "1abc", None, None),
        ("input", ":r1:", "email", 'input[name="email"]'),
        ("input", None, 'bad"name', None),
        ("div", None, "email", None),
    ],
)
def test_css_prefers_stable_ids_then_named_controls(
    tag: str, element_id: str | None, name: str | None, expected: str | None
) -> None:
    assert css_selector(tag, element_id, name) == expected


def test_scopes_put_rows_first_then_named_containers_then_test_ids_then_ids() -> None:
    ancestors = [
        AncestorFacts(tag="td", role="cell", name="View order PO-1042"),
        AncestorFacts(tag="tr", role="row", name="PO-1042 …", row_header=" PO-1042 "),
        AncestorFacts(tag="tbody", role="rowgroup", name="", data_testid="rows", id="rows"),
        AncestorFacts(tag="section", role="region", name="Recent orders", id=":r5:"),
    ]

    scopes = scope_selectors(ancestors)

    assert scopes == (
        (role("row", "PO-1042", exact=False), 2),
        (role("region", "Recent orders"), 4),
        (by_test_id("rows"), 3),
        (css("#rows"), 3),
    )


def test_with_scope_nests_up_to_two_levels() -> None:
    inner = with_scope(role("button", "View", exact=False), role("row", "PO-1042", exact=False))
    assert inner is not None
    outer = with_scope(role("row", "PO-1042", exact=False), role("table", "Orders"))
    assert outer is not None

    two = with_scope(role("button", "View", exact=False), outer)
    assert two is not None
    three_deep = with_scope(by_test_id("rows"), outer)
    assert three_deep is not None
    assert with_scope(role("button", "View", exact=False), three_deep) is None


def test_summaries_read_like_selectors() -> None:
    scoped = with_scope(role("button", "View", exact=False), role("row", "PO-1042", exact=False))
    assert scoped is not None

    assert summarize(scoped) == (
        "role_name button 'View' (substring) within role_name row 'PO-1042' (substring)"
    )
    assert summarize(css("#save")) == "css #save"
    assert summarize(by_test_id("save")) == "test_id 'save'"
    assert summarize(selector(strategy="label", value="Email", exact=False)) == (
        "label 'Email' (substring)"
    )


optional_text = st.one_of(st.none(), st.text(max_size=40))


@given(
    testid=optional_text,
    name=st.text(max_size=40),
    own=optional_text,
    label=optional_text,
    placeholder=optional_text,
    rendered=optional_text,
    element_id=optional_text,
    text_entry=st.booleans(),
    tag=st.sampled_from(["button", "input", "a", "div"]),
)
def test_candidates_are_ranked_unique_and_bounded(
    testid: str | None,
    name: str,
    own: str | None,
    label: str | None,
    placeholder: str | None,
    rendered: str | None,
    element_id: str | None,
    text_entry: bool,
    tag: str,
) -> None:
    found = candidate_selectors(
        facts(
            tag,
            data_testid=testid,
            own_text=own,
            label_text=label,
            placeholder=placeholder,
            text=rendered,
            id=element_id,
            text_entry=text_entry,
        ),
        identity("button", name),
    )

    ranks = [RANK.index(candidate.strategy) for candidate in found]
    assert ranks == sorted(ranks)
    assert len(set(found)) == len(found) <= 10
