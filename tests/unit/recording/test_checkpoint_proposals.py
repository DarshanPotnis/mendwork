"""Checkpoint proposals from before and after a step, and keeping only those that pass."""

from pathlib import Path

import pytest

from mendwork.engine.domain.checkpoints import TextPresent
from mendwork.engine.domain.enums import CheckpointKind
from mendwork.engine.ports.browser_types import DownloadObservation
from mendwork.engine.ports.recording_types import Landmark, PageObservation
from mendwork.engine.recording.checkpoints import (
    Proposal,
    keep_passing,
    new_landmarks,
    new_live_text,
    propose_after_action,
    propose_after_navigation,
    url_pattern,
)
from tests.fakes.browser import FakeElement
from tests.unit.recording.builders import browser, context, heading, role


def observation(
    url: str = "https://portal.example.test/dashboard.html", **fields: object
) -> PageObservation:
    return PageObservation.model_validate({"url": url, "navigation": 1, **fields})


def kinds(proposals: tuple[Proposal, ...]) -> list[CheckpointKind]:
    return [proposal.kind for proposal in proposals]


def test_url_patterns_match_the_path_on_any_host() -> None:
    pattern = url_pattern("https://portal.example.test/dashboard.html?seed=1#main")
    assert pattern == r"https?://[^?#]+/dashboard\.html(?:[?#].*)?"
    assert url_pattern("about:blank") is None
    assert url_pattern("http://127.0.0.1:8765") == r"https?://[^?#]+/(?:[?#].*)?"


def test_a_click_that_navigates_proposes_its_url_and_the_new_heading() -> None:
    before = observation("https://portal.example.test/index.html", landmarks=[heading("Sign in")])
    after = observation(
        landmarks=[
            Landmark(role="navigation", name="Primary"),
            heading("Welcome back"),
            heading("Sign in"),
            Landmark(role="generic", name="ignored"),
        ],
        live_texts=["18 shipments"],
    )

    proposals = propose_after_action(
        before, after, navigated=True, download=None, submits_form=True
    )

    assert kinds(proposals) == [
        CheckpointKind.URL_MATCHES,
        CheckpointKind.ELEMENT_VISIBLE,
        CheckpointKind.NO_ERROR_BANNER,
    ]
    visible = proposals[1].alternatives
    assert [alternative.model_dump(mode="json")["selector"]["name"] for alternative in visible] == [
        "Welcome back",
        "Primary",
    ]


def test_a_click_that_changes_a_live_region_proposes_its_new_text() -> None:
    before = observation(live_texts=["18 shipments between Jan 1 and Mar 31."])
    after = observation(live_texts=["", "14 shipments  between Feb 10 and Apr 20."])

    proposals = propose_after_action(
        before, after, navigated=False, download=None, submits_form=False
    )

    assert proposals == (
        Proposal(
            kind=CheckpointKind.TEXT_PRESENT,
            alternatives=(
                TextPresent(
                    kind=CheckpointKind.TEXT_PRESENT, text="14 shipments between Feb 10 and Apr 20."
                ),
            ),
        ),
    )


def test_downloads_propose_their_exact_file_name_and_failed_ones_nothing() -> None:
    page = observation()
    done = DownloadObservation(
        suggested_filename="shipments_2026-02-10.csv", path=Path("downloads/d")
    )
    failed = DownloadObservation(suggested_filename="x.csv", failure="canceled")

    proposals = propose_after_action(page, page, navigated=False, download=done, submits_form=False)
    assert kinds(proposals) == [CheckpointKind.DOWNLOAD_COMPLETED]
    assert proposals[0].alternatives[0].model_dump()["filename_pattern"] == (
        r"shipments_2026\-02\-10\.csv"
    )
    assert (
        propose_after_action(page, page, navigated=False, download=failed, submits_form=False) == ()
    )


def test_a_navigation_proposes_its_first_heading() -> None:
    after = observation(landmarks=[Landmark(role="main", name="Main"), heading("Sign in")])

    proposals = propose_after_navigation(after)

    assert kinds(proposals) == [CheckpointKind.ELEMENT_VISIBLE]
    assert len(proposals[0].alternatives) == 2


def test_new_landmarks_ignore_case_and_whitespace_changes() -> None:
    before = observation(landmarks=[heading("Date  range")])
    after = observation(landmarks=[heading("date range"), heading("Results")])

    assert new_landmarks(before, after) == (heading("Results"),)


def test_a_repeated_new_landmark_is_proposed_once() -> None:
    after = observation(landmarks=[heading("Results"), heading("results "), heading("")])

    assert new_landmarks(None, after) == (heading("Results"),)


def test_repeated_live_texts_count_as_new_only_beyond_what_was_there() -> None:
    before = observation(live_texts=["Saved", "Saved"])

    assert new_live_text(before, observation(live_texts=["Saved", "Saved"])) is None
    assert new_live_text(before, observation(live_texts=["Saved", "Saved", "Saved"])) == "Saved"


@pytest.mark.asyncio
async def test_an_ambiguous_heading_gives_way_to_the_next_alternative() -> None:
    page = browser()
    page.elements["results"] = FakeElement(tag="h2", role="heading", name="Results")
    page.finds[role("heading", "Filters")] = (2,)
    page.finds[role("heading", "Results")] = "results"
    after = observation(landmarks=[heading("Filters"), heading("Results")])

    kept, dropped = await keep_passing(propose_after_navigation(after), context(page).checkpoints())

    assert [checkpoint.model_dump(mode="json")["selector"]["name"] for checkpoint in kept] == [
        "Results"
    ]
    assert dropped == ()


@pytest.mark.asyncio
async def test_a_proposal_that_never_passes_is_dropped_with_its_reason() -> None:
    page = browser(text="nothing here")
    after = observation(live_texts=["Saved"])
    before = observation()
    proposals = propose_after_action(
        before, after, navigated=False, download=None, submits_form=False
    )

    kept, dropped = await keep_passing(proposals, context(page).checkpoints())

    assert kept == ()
    assert [(item.kind, item.reason) for item in dropped] == [
        (CheckpointKind.TEXT_PRESENT, "did not pass (timeout)")
    ]


@pytest.mark.asyncio
async def test_downloads_are_checked_against_the_download_that_was_collected() -> None:
    page = observation()
    done = DownloadObservation(suggested_filename="report.csv", path=Path("downloads/report"))
    proposals = propose_after_action(page, page, navigated=False, download=done, submits_form=False)
    checks = context(browser()).checkpoints()

    kept, _ = await keep_passing(proposals, checks, download=done)
    assert len(kept) == 1

    other = DownloadObservation(suggested_filename="other.csv", path=Path("downloads/other"))
    assert (await keep_passing(proposals, checks, download=other))[1][0].reason == (
        "the file name did not match"
    )
    assert (await keep_passing(proposals, checks, download=None))[1][0].reason == (
        "no download completed"
    )
