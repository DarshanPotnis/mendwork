"""Rung 0 resolution against a scripted page: consensus, identity, waiting, and stability."""

import pytest

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.errors import AmbiguousTarget, PageNeverStable, TargetDrifted, TargetNotFound
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.rung0 import ResolvedTarget, resolve_target
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser, FakeElement
from tests.unit.replay.builders import (
    CSS,
    ROLE_NAME,
    TEST_ID,
    browser,
    button,
    button_fingerprint,
    password_fingerprint,
)

pytestmark = pytest.mark.asyncio


async def resolve(
    page: FakeBrowser, fingerprint: Fingerprint | None = None, *, timeout_ms: int = 1_000
) -> ResolvedTarget:
    return await resolve_target(
        page,
        fingerprint or button_fingerprint(),
        deadline=Deadline.after(page.timer, timeout_ms),
        settle_timeout_ms=100,
        quiet_frames=2,
        scrubber=SecretScrubber(),
    )


async def test_agreeing_hits_resolve_to_the_element_found_by_the_best_rank() -> None:
    page = browser(elements={"csv": button()}, finds={TEST_ID: "csv", ROLE_NAME: "csv", CSS: "csv"})

    resolved = await resolve(page)

    assert (resolved.evidence.resolved_rank, resolved.selector) == (0, TEST_ID)
    assert [report.outcome for report in resolved.evidence.selectors] == ["hit", "hit", "hit"]
    assert resolved.evidence.identity is not None
    assert resolved.evidence.identity.name == "Download CSV"


async def test_a_lower_ranked_selector_resolves_when_better_ones_miss() -> None:
    page = browser(elements={"csv": button()}, finds={ROLE_NAME: "csv"})

    resolved = await resolve(page)

    assert resolved.evidence.resolved_rank == 1
    assert [report.level_counts for report in resolved.evidence.selectors] == [(0,), (1,), (0,)]


async def test_selectors_that_find_different_elements_are_ambiguous_and_evidenced() -> None:
    page = browser(
        elements={"csv": button(), "decoy": button("Download CSV copy")},
        finds={TEST_ID: "csv", ROLE_NAME: "decoy", CSS: "csv"},
    )

    with pytest.raises(AmbiguousTarget) as caught:
        await resolve(page)

    context = caught.value.context
    assert (context["reason"], context["groups"]) == ("selectors_disagree", [[0, 2], [1]])
    names = [element["name"] for element in context["target"]["elements"]]  # type: ignore[index]
    assert names == ["Download CSV", "Download CSV copy"]


async def test_several_matches_and_no_hit_are_ambiguous() -> None:
    page = browser(elements={}, finds={ROLE_NAME: (2,), CSS: (0,)})

    with pytest.raises(AmbiguousTarget) as caught:
        await resolve(page)

    assert (caught.value.context["reason"], caught.value.context["ranks"]) == (
        "several_matches",
        [1],
    )


async def test_a_quiet_ambiguous_page_fails_without_waiting() -> None:
    page = browser(elements={}, finds={ROLE_NAME: (2,)})

    with pytest.raises(AmbiguousTarget):
        await resolve(page)

    assert page.calls_named("wait_for_dom_change") == []


async def test_a_busy_ambiguous_page_is_given_until_the_deadline_to_settle() -> None:
    page = browser(elements={"csv": button()}, finds={ROLE_NAME: (2,)}, quiet=False)
    page.changes.append(lambda b: b.finds.update({ROLE_NAME: "csv"}))

    resolved = await resolve(page)

    assert resolved.evidence.resolved_rank == 1


async def test_nothing_found_waits_for_the_page_to_change_until_the_deadline() -> None:
    page = browser(elements={}, finds={})

    with pytest.raises(TargetNotFound) as caught:
        await resolve(page, timeout_ms=1_500)

    assert caught.value.context["reason"] == "no_match"
    assert caught.value.context["waited_ms"] >= 1_500  # type: ignore[operator]


async def test_a_target_that_appears_later_is_resolved() -> None:
    page = browser(elements={"csv": button()}, finds={})
    page.changes.append(lambda b: b.finds.update({CSS: "csv"}))

    resolved = await resolve(page)

    assert resolved.evidence.resolved_rank == 2


async def test_a_renamed_element_is_a_drifted_match_with_recorded_and_found_identity() -> None:
    page = browser(elements={"csv": button("Delete data")}, finds={TEST_ID: "csv", CSS: "csv"})

    with pytest.raises(TargetDrifted) as caught:
        await resolve(page)

    context = caught.value.context
    assert context["recorded"] == {
        "tag": "button",
        "input_type": None,
        "role": "button",
        "name": "Download CSV",
        "confirmed": None,
    }
    assert context["found"]["name"] == "Delete data"  # type: ignore[index]
    assert context["differences"] == ["accessible_name"]
    assert context["target"]["resolved_rank"] == 0  # type: ignore[index]


async def test_a_role_less_password_field_resolves_by_tag_and_type() -> None:
    field = FakeElement(tag="input", input_type="password", role="textbox", name="Password")
    page = browser(elements={"password": field}, finds={TEST_ID: "password"})

    resolved = await resolve(page, password_fingerprint((TEST_ID,)))

    assert resolved.identity.role == "textbox"


async def test_an_identity_playwright_does_not_confirm_is_never_resolved() -> None:
    page = browser(elements={"csv": button(confirmed=False)}, finds={TEST_ID: "csv"})

    with pytest.raises(TargetDrifted) as caught:
        await resolve(page)

    assert caught.value.context["differences"] == ["unconfirmed"]


async def test_a_snapshot_the_page_changed_during_is_discarded_and_read_again() -> None:
    page = browser(elements={"csv": button()}, finds={TEST_ID: "csv"}, unstable_reads=1)

    resolved = await resolve(page)

    assert resolved.evidence.resolved_rank == 0
    assert page.calls_named("settle") == ["settle", "settle"]


async def test_a_page_that_changes_during_every_attempt_never_resolves() -> None:
    page = browser(elements={"csv": button()}, finds={TEST_ID: "csv"}, unstable_reads=1_000)

    with pytest.raises(PageNeverStable) as caught:
        await resolve(page)

    assert (
        caught.value.message == "the page kept changing, so the target could not be verified safely"
    )


async def test_an_unstable_page_reports_the_last_stable_verdict_at_the_deadline() -> None:
    page = browser(elements={}, finds={})

    def destabilize(fake: FakeBrowser) -> None:
        fake.unstable_reads = 1_000

    page.changes.append(destabilize)

    with pytest.raises(TargetNotFound):
        await resolve(page)


async def test_every_element_but_the_winner_is_released() -> None:
    page = browser(elements={"csv": button()}, finds={TEST_ID: "csv", ROLE_NAME: "csv", CSS: "csv"})

    resolved = await resolve(page)

    assert resolved.element not in page.released
    assert len(page.released) == 2
