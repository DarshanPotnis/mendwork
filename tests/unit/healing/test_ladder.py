"""The heal ladder on a scripted page: rung order, seeds, the cap, look-alikes, and releases."""

from collections.abc import Mapping
from typing import Final

import pytest

from mendwork.engine.domain.fingerprint import Fingerprint
from mendwork.engine.domain.heals import (
    AbstentionReason,
    CandidateOrigin,
    RejectionReason,
    RungOutcome,
)
from mendwork.engine.domain.steps import Step, step_target
from mendwork.engine.domain.targets import SelectorOutcome, SelectorReport, TargetEvidence
from mendwork.engine.errors import (
    AmbiguousTarget,
    MendworkError,
    PageNeverStable,
    TargetDrifted,
    TargetNotFound,
)
from mendwork.engine.healing.candidates import candidate_signature
from mendwork.engine.healing.config import HealingConfig
from mendwork.engine.healing.context import ClimbRequest, LadderContext
from mendwork.engine.healing.ladder import ClimbResult, climb, is_healable, rung0_report
from mendwork.engine.ports.element_types import Box
from mendwork.engine.replay.deadlines import Deadline
from mendwork.engine.replay.reports import TARGET_CONTEXT_KEY
from mendwork.engine.safety.secret_scrub import SecretScrubber
from tests.fakes.browser import FakeBrowser, FakeElement
from tests.unit.healing.builders import (
    EXPORT_TEST_ID,
    export_button,
    live,
    reference_field,
    step,
)
from tests.unit.replay.builders import browser, healing, selector
from tests.workflows import click_step

pytestmark = pytest.mark.asyncio

RECORDED: Final = export_button(selectors=[EXPORT_TEST_ID.model_dump()])
ROLE_ALTERNATE: Final = selector(strategy="role_name", role="button", name="Export ledger")
TEXT_ALTERNATE: Final = selector(strategy="text", value="Export ledger")
CHECKED: Final = [{"kind": "text_present", "text": "Ledger exported"}]
FULLY_RECORDED: Final = export_button(
    selectors=[
        EXPORT_TEST_ID.model_dump(),
        ROLE_ALTERNATE.model_dump(),
        TEXT_ALTERNATE.model_dump(),
        selector(strategy="css", value="#export-ledger").model_dump(),
    ]
)


def export_step(fingerprint: Fingerprint = RECORDED) -> Step:
    return step(click_step(target=fingerprint.model_dump(), checkpoints=CHECKED))


def add(
    page: FakeBrowser,
    key: str,
    *,
    fingerprint: Fingerprint = RECORDED,
    identity: Mapping[str, object] | None = None,
    facts: Mapping[str, object] | None = None,
    candidate: bool = True,
    confirmed: bool | None = True,
) -> None:
    found = live(fingerprint, identity=identity, facts=facts)
    page.elements[key] = FakeElement(
        tag=found.identity.tag,
        name=found.identity.name,
        role=found.identity.role,
        input_type=found.identity.input_type,
        confirmed=confirmed,
    )
    page.facts[key] = found.facts
    if candidate:
        page.candidates.append(key)


def not_found() -> TargetNotFound:
    return TargetNotFound("nothing", reason="no_match", **{TARGET_CONTEXT_KEY: {"selectors": []}})


def drifted(rank: int = 0) -> TargetDrifted:
    evidence = TargetEvidence(
        selectors=(
            SelectorReport(
                rank=rank,
                strategy="test_id",
                level_counts=(1,),
                outcome=SelectorOutcome.HIT,
                element=0,
            ),
        ),
        resolved_rank=rank,
        differences=("accessible_name",),
    )
    return TargetDrifted(
        "drifted",
        reason="identity_changed",
        **{TARGET_CONTEXT_KEY: evidence.model_dump(mode="json")},
    )


async def run(
    page: FakeBrowser,
    failure: MendworkError,
    *,
    target: Step | None = None,
    config: HealingConfig | None = None,
    excluded: frozenset[tuple[str, ...]] = frozenset(),
) -> ClimbResult:
    action = target or export_step()
    fingerprint = step_target(action)
    assert fingerprint is not None
    context = LadderContext(
        browser=page,
        config=config or healing(),
        settle_timeout_ms=100,
        quiet_frames=2,
        scrubber=SecretScrubber(),
    )
    request = ClimbRequest(
        step=action,
        fingerprint=fingerprint,
        attempt=1,
        excluded=excluded,
        deadline=Deadline.after(page.timer, 5_000),
    )
    return await climb(context, request, failure)


def rungs(result: ClimbResult) -> list[tuple[int, RungOutcome]]:
    return [(report.rung, report.outcome) for report in result.reports]


async def test_rung1_accepts_the_recorded_identity_found_by_a_selector_it_did_not_record() -> None:
    page = browser()
    add(page, "export")
    page.finds[ROLE_ALTERNATE] = "export"

    result = await run(page, not_found())

    assert rungs(result) == [(0, RungOutcome.NOT_FOUND), (1, RungOutcome.RESOLVED)]
    assert result.accepted is not None
    assert (result.accepted.rung, result.accepted.scored.origin) == (1, CandidateOrigin.RUNG1)
    assert page.scans == []


async def test_rung1_hands_a_drifted_identity_to_rung2_which_compares_it() -> None:
    page = browser()
    add(
        page,
        "export",
        identity={"name": "Export ledgers"},
        facts={"text": "Export ledgers"},
        candidate=False,
    )
    page.finds[TEXT_ALTERNATE] = "export"

    result = await run(page, not_found())

    assert rungs(result) == [
        (0, RungOutcome.NOT_FOUND),
        (1, RungOutcome.DRIFTED),
        (2, RungOutcome.RESOLVED),
    ]
    assert result.accepted is not None
    assert result.accepted.scored.origin is CandidateOrigin.RUNG1


async def test_a_drifted_rung0_match_is_compared_like_any_other_candidate() -> None:
    page = browser()
    add(
        page,
        "export",
        identity={"name": "Share ledger"},
        facts={"text": "Share ledger"},
        candidate=False,
    )
    add(
        page,
        "neighbour",
        identity={"name": "Invite teammate"},
        facts={
            "text": "Invite teammate",
            "id": None,
            "data_testid": None,
            "box": Box(x=0.1, y=0.8, width=0.1, height=0.05),
        },
    )
    page.finds[EXPORT_TEST_ID] = "export"

    result = await run(page, drifted())

    assert rungs(result)[0] == (0, RungOutcome.DRIFTED)
    assert rungs(result)[-1] == (2, RungOutcome.RESOLVED)
    accepted = result.accepted
    assert accepted is not None
    assert accepted.scored.origin is CandidateOrigin.RUNG0_DRIFTED
    assert accepted.scored.score == pytest.approx(0.70)
    assert accepted.report.candidates[0].origin is CandidateOrigin.RUNG0_DRIFTED
    assert accepted.report.runner_up == "c2"


async def test_a_dangerously_renamed_rung0_match_stops_the_ladder() -> None:
    page = browser()
    add(page, "export", identity={"name": "Delete ledger"}, facts={"text": "Delete ledger"})
    page.finds[EXPORT_TEST_ID] = "export"

    result = await run(page, drifted())

    assert result.accepted is None
    assert result.abstention is AbstentionReason.TOP_REJECTED
    top = result.reports[-1].candidates[0]
    assert top.rejection is not None
    assert top.rejection.reason is RejectionReason.DANGER_WORD
    assert page.key_of(page.released[-1]) == "export"


async def test_two_look_alikes_closer_than_the_margin_abstain() -> None:
    page = browser()
    add(page, "left")
    add(page, "right", facts={"box": Box(x=0.72, y=0.3, width=0.12, height=0.05)})

    result = await run(page, not_found())

    assert result.abstention is AbstentionReason.BELOW_MARGIN
    report = result.reports[-1]
    assert (report.considered, report.on_page) == (2, 2)
    assert report.margin == pytest.approx(0.048)


async def test_candidates_the_action_cannot_receive_are_released_and_nothing_is_left() -> None:
    page = browser()
    add(page, "field", fingerprint=reference_field())

    result = await run(page, not_found())

    assert result.abstention is AbstentionReason.NO_CANDIDATES
    assert [page.key_of(ref) for ref in page.released] == ["field"]


async def test_a_page_with_more_candidates_than_the_cap_is_never_healed() -> None:
    page = browser()
    for key in ("a", "b", "c"):
        add(page, key)

    result = await run(page, not_found(), config=healing(candidates_max=2))

    assert result.abstention is AbstentionReason.CANDIDATE_CAP_REACHED
    assert (result.reports[-1].on_page, result.reports[-1].candidates) == (3, ())
    assert sorted(page.key_of(ref) for ref in page.released) == ["a", "b"]


async def test_a_winner_playwright_cannot_confirm_is_refused() -> None:
    page = browser()
    add(page, "export", confirmed=False)

    result = await run(page, not_found())

    assert result.abstention is AbstentionReason.TOP_REJECTED
    rejection = result.reports[-1].candidates[0].rejection
    assert rejection is not None
    assert rejection.reason is RejectionReason.UNCONFIRMED_IDENTITY


async def test_a_candidate_that_failed_verification_is_left_out() -> None:
    page = browser()
    add(page, "export")
    signature = candidate_signature(live(RECORDED))

    result = await run(page, not_found(), excluded=frozenset({signature}))

    assert result.abstention is AbstentionReason.NO_CANDIDATES
    assert result.reports[-1].considered == 0


async def test_rung1_leaves_out_an_identity_that_failed_verification() -> None:
    page = browser()
    add(page, "export", candidate=False)
    page.finds[ROLE_ALTERNATE] = "export"

    result = await run(page, not_found(), excluded=frozenset({candidate_signature(live(RECORDED))}))

    assert rungs(result)[1] == (1, RungOutcome.NOT_FOUND)
    assert result.abstention is AbstentionReason.NO_CANDIDATES


async def test_rung1_seeds_rung2_with_an_identity_that_was_refused_or_scored_too_low() -> None:
    page = browser()
    fingerprint = export_button(
        selectors=[EXPORT_TEST_ID.model_dump()],
        text="Export ledger 2025",
        accessible_name="Export ledger",
    )
    add(
        page,
        "export",
        fingerprint=fingerprint,
        facts={
            "text": "Export ledger 2026",
            "id": None,
            "data_testid": None,
            "box": Box(x=0.0, y=0.9, width=0.1, height=0.05),
            "nearby_text": (),
            "structural_path": "footer > button",
        },
        candidate=False,
    )
    page.finds[ROLE_ALTERNATE] = "export"

    result = await run(page, not_found(), target=export_step(fingerprint))

    assert rungs(result)[1] == (1, RungOutcome.TOP_REJECTED)
    assert rungs(result)[2] == (2, RungOutcome.TOP_REJECTED)


async def test_rung1_seeds_rung2_with_a_low_scoring_identity() -> None:
    page = browser()
    far = {
        "id": None,
        "data_testid": None,
        "nearby_text": (),
        "structural_path": "footer > button",
        "box": Box(x=0.0, y=0.9, width=0.1, height=0.05),
    }
    add(page, "export", facts=far, candidate=False)
    page.finds[ROLE_ALTERNATE] = "export"

    result = await run(page, not_found())

    assert rungs(result)[1:] == [(1, RungOutcome.BELOW_THRESHOLD), (2, RungOutcome.BELOW_THRESHOLD)]
    assert result.abstention is AbstentionReason.BELOW_THRESHOLD


async def test_alternates_that_disagree_are_ambiguous_at_rung1() -> None:
    page = browser()
    add(page, "export")
    add(page, "other", identity={"name": "Export ledger"}, candidate=False)
    page.finds[ROLE_ALTERNATE] = "export"
    page.finds[TEXT_ALTERNATE] = "other"

    result = await run(page, not_found())

    assert rungs(result)[1] == (1, RungOutcome.AMBIGUOUS)


async def test_an_element_whose_facts_vanish_is_not_compared() -> None:
    page = browser()
    add(page, "export", candidate=False)
    page.finds[ROLE_ALTERNATE] = "export"
    page.finds[EXPORT_TEST_ID] = "export"
    page.detached.add("export")

    result = await run(page, drifted())

    assert rungs(result)[1] == (1, RungOutcome.NOT_FOUND)
    assert result.abstention is AbstentionReason.NO_CANDIDATES


async def test_a_fingerprint_with_nothing_unrecorded_has_no_rung1_alternates() -> None:
    fingerprint = FULLY_RECORDED
    page = browser()

    result = await run(page, not_found(), target=export_step(fingerprint))

    assert rungs(result)[1] == (1, RungOutcome.NOT_FOUND)
    first = result.reports[1].target
    assert first is not None
    assert first.selectors == ()


async def test_a_node_found_by_a_seed_and_the_scan_is_compared_once_as_the_seed() -> None:
    page = browser()
    add(page, "export", identity={"name": "Share ledger"}, facts={"text": "Share ledger"})
    page.finds[EXPORT_TEST_ID] = "export"

    result = await run(page, drifted())

    report = result.reports[-1]
    assert report.considered == 1
    assert report.candidates[0].origin is CandidateOrigin.RUNG0_DRIFTED


async def test_a_page_that_never_settles_abstains_at_the_rung_reading_it() -> None:
    page = browser(quiet=False, unstable_reads=10_000)
    add(page, "export")

    at_rung1 = await run(page, not_found())
    at_rung2 = await run(page, not_found(), target=export_step(FULLY_RECORDED))

    assert at_rung1.abstention is AbstentionReason.PAGE_NEVER_STABLE
    assert rungs(at_rung1)[-1] == (1, RungOutcome.PAGE_NEVER_STABLE)
    assert rungs(at_rung2)[-1] == (2, RungOutcome.PAGE_NEVER_STABLE)


async def test_a_winner_changing_kind_reports_the_change() -> None:
    page = browser()
    add(
        page,
        "export",
        identity={"tag": "a", "role": "link", "input_type": None},
        facts={"href": "/ledger", "structural_path": "main > article > div > a"},
    )

    result = await run(page, not_found())

    assert result.accepted is not None
    assert result.accepted.report.kind_change == "button → link"


@pytest.mark.parametrize(
    ("error", "healable"),
    [
        (TargetNotFound("x", reason="no_match"), True),
        (AmbiguousTarget("x", reason="several_matches"), True),
        (TargetDrifted("x", reason="identity_changed"), True),
        (TargetDrifted("x", reason="changed_before_action"), False),
        (TargetNotFound("x", reason="detached_before_action"), False),
        (PageNeverStable("x"), False),
    ],
)
async def test_only_rung0_resolution_failures_are_healed(
    error: MendworkError, healable: bool
) -> None:
    assert is_healable(error) is healable


async def test_rung0_reports_its_outcome_and_evidence() -> None:
    assert rung0_report(not_found(), 1).outcome is RungOutcome.NOT_FOUND
    assert rung0_report(AmbiguousTarget("x"), 2).outcome is RungOutcome.AMBIGUOUS
    report = rung0_report(drifted(), 1)
    assert report.outcome is RungOutcome.DRIFTED
    assert report.target is not None
    assert report.target.resolved_rank == 0
