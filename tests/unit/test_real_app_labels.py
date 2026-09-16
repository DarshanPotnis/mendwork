"""A person's labels for a release, the approval that freezes them, and what scoring refuses.

Ground truth on a real application is a person's, so the only thing that makes it trustworthy is
that it cannot be changed after they approved it, or quietly pointed at another release. These are
the checks that enforce that (ADR 0014).
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from benchmarks.real_apps.gitea.pair import PAIR, RELEASE_A, RELEASE_B, WORKFLOW_ID, plan
from benchmarks.real_apps.labels import (
    Expect,
    LabelsNotApprovedError,
    PairLabels,
    StepLabel,
    approval_for,
    approved,
    load_approval,
    load_labels,
    write_approval,
)
from benchmarks.real_apps.run import ScoringRefusedError, check_plan, check_settings
from mendwork.engine.benchmark.truth import Expectation
from mendwork.settings import Settings

MAX_BYTES = 1 << 20


def labels(**changes: object) -> PairLabels:
    base = {
        "pair": PAIR,
        "release": RELEASE_B.label,
        "workflow_id": WORKFLOW_ID,
        "labelled_on": "2026-09-15",
        "method": "From the release's own pages, before Mendwork ran on it.",
        "steps": (
            StepLabel(
                step_id="click_sign_in", expect=Expect.ACT, selector="#go", note="The button."
            ),
            StepLabel(step_id="click_gone", expect=Expect.ABSTAIN, note="No such control here."),
        ),
    }
    return PairLabels.model_validate({**base, **changes})


def test_a_step_to_act_on_needs_a_selector_and_a_step_to_abstain_must_not_have_one() -> None:
    with pytest.raises(ValidationError, match="needs a selector, and only it"):
        StepLabel(step_id="s", expect=Expect.ACT, note="n")
    with pytest.raises(ValidationError, match="needs a selector, and only it"):
        StepLabel(step_id="s", expect=Expect.ABSTAIN, selector="#x", note="n")


def test_each_step_is_labelled_once() -> None:
    with pytest.raises(ValidationError, match="each step is labelled once"):
        labels(
            steps=(
                StepLabel(step_id="same", expect=Expect.ACT, selector="#a", note="n"),
                StepLabel(step_id="same", expect=Expect.ACT, selector="#b", note="n"),
            )
        )


def test_labels_become_ground_truth_with_a_key_and_an_expectation_per_step() -> None:
    truths = labels().truths()

    assert truths["click_sign_in"].expectation is Expectation.ACT
    assert truths["click_sign_in"].target_key == "click_sign_in"
    assert truths["click_gone"].expectation is Expectation.ABSTAIN
    assert labels().selectors() == {"click_sign_in": "#go"}


def test_a_changed_control_is_named_as_the_releases_own_change() -> None:
    changed = labels(
        steps=(
            StepLabel(
                step_id="click_sign_in",
                expect=Expect.ACT,
                selector="#go",
                changed=True,
                note="The class changed.",
            ),
        )
    )

    assert changed.truths()["click_sign_in"].changes == ("changed_by_the_release",)


def test_the_digest_follows_every_labelled_word() -> None:
    before = labels().digest

    reworded = labels(
        steps=(
            StepLabel(
                step_id="click_sign_in", expect=Expect.ACT, selector="#go", note="A different note."
            ),
            StepLabel(step_id="click_gone", expect=Expect.ABSTAIN, note="No such control here."),
        )
    )

    assert before.startswith("sha256:")
    assert reworded.digest != before


def test_nothing_is_scored_without_an_approval_or_after_the_labels_changed() -> None:
    approval = approval_for(
        labels(),
        by="A person",
        at=datetime(2026, 9, 15, tzinfo=UTC),
        walked=("click_sign_in",),
        screenshots={"click_sign_in": "sha256:aa"},
    )

    assert approved(labels(), approval) is approval
    with pytest.raises(LabelsNotApprovedError, match="are not approved"):
        approved(labels(), None)
    with pytest.raises(LabelsNotApprovedError, match="changed after they were approved"):
        approved(labels(labelled_on="2026-09-16"), approval)


def test_an_approval_is_written_and_read_back_unchanged(tmp_path: Path) -> None:
    approval = approval_for(
        labels(),
        by="A person",
        at=datetime(2026, 9, 15, tzinfo=UTC),
        walked=("click_sign_in",),
        screenshots={"click_sign_in": "sha256:aa"},
    )
    path = tmp_path / "labels.approval.json"

    write_approval(path, approval)

    assert load_approval(path) == approval


def test_the_committed_gitea_labels_are_approved_as_they_stand() -> None:
    """The published pair's labels still match the approval the repository owner gave."""
    gitea = plan(("ladder_free",), max_bytes=MAX_BYTES)

    approval = check_plan(gitea)

    assert approval.labels_digest == gitea.labels.digest
    assert len(approval.screenshots) == len(gitea.workflow.steps)
    assert set(gitea.labels.selectors()) <= {step.step_id for step in gitea.labels.steps}


def test_every_step_that_acts_must_be_labelled() -> None:
    gitea = plan(("ladder_free",), max_bytes=MAX_BYTES)
    short = gitea.labels.model_copy(update={"steps": gitea.labels.steps[:-1]})

    with pytest.raises(ScoringRefusedError, match="unlabelled"):
        check_plan(
            type(gitea)(
                **{
                    **{f.name: getattr(gitea, f.name) for f in gitea.__dataclass_fields__.values()},
                    "labels": short,
                }
            )
        )


def test_labels_for_another_release_or_workflow_are_refused() -> None:
    gitea = plan(("ladder_free",), max_bytes=MAX_BYTES)
    fields = {f.name: getattr(gitea, f.name) for f in gitea.__dataclass_fields__.values()}

    with pytest.raises(ScoringRefusedError, match=f"not {PAIR} {RELEASE_A.label}"):
        check_plan(type(gitea)(**{**fields, "release": RELEASE_A}))
    other = gitea.labels.model_copy(update={"workflow_id": "something_else"})
    with pytest.raises(ScoringRefusedError, match="the labels are for something_else"):
        check_plan(type(gitea)(**{**fields, "labels": other}))


def test_a_real_app_run_refuses_settings_that_could_tune_it_to_the_release() -> None:
    check_settings(Settings().model_copy(update={"trace_on_failure": False}))

    with pytest.raises(ScoringRefusedError, match="step_timeout_ms"):
        check_settings(Settings().model_copy(update={"step_timeout_ms": 30_000}))


def test_the_committed_labels_file_loads_and_says_how_it_was_written() -> None:
    from benchmarks.real_apps.gitea.pair import labels_path

    committed = load_labels(labels_path(RELEASE_B))

    assert committed.release == RELEASE_B.label
    assert "Mendwork" in committed.method
    assert committed.digest.startswith("sha256:")


def test_provenance_names_both_releases_images_and_the_labels_it_scored_against() -> None:
    from benchmarks.real_apps.run import source_digests

    gitea = plan(("ladder_free",), max_bytes=MAX_BYTES)

    digests = source_digests(gitea, Path.cwd())

    assert digests[f"image:{PAIR} {RELEASE_A.label} (recorded on)"] == RELEASE_A.image
    assert digests[f"image:{PAIR} {RELEASE_B.label} (replayed on)"] == RELEASE_B.image
    assert RELEASE_A.image != RELEASE_B.image
    assert digests[f"labels:{PAIR} {RELEASE_B.label}"] == gitea.labels.digest
