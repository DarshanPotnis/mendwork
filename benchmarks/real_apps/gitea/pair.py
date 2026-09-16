"""The Gitea release pair: two pinned releases, and the task a person does on them (ADR 0014).

Gitea 1.19.4 and 1.22.6 are our own local instances of MIT-licensed software, so no third
party's service is touched. Between these releases Gitea kept its wording and rewrote its markup:
the sign-in button's classes changed (``ui green button`` to ``ui primary button``), the
repository's Issues tab kept its accessible name but moved into an overflow menu, the New Issue
link's classes changed, and the dashboard's repository list became a component whose link now
carries the same accessible name as the activity feed's link to the same repository. The workflow
below acts on each of those, and ends by creating the issue: the one irreversible step in the whole
benchmark, where a heal must stop for a person.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from benchmarks.real_apps.instances import ADMIN_PASSWORD, ADMIN_USER, REPOSITORY, Release
from benchmarks.real_apps.labels import LabelApproval, PairLabels, load_approval, load_labels
from benchmarks.real_apps.record import ScriptedPerson
from benchmarks.real_apps.run import RealAppPlan
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec

PAIR: Final = "gitea"
WORKFLOW_ID: Final = "gitea_new_issue_form"
RELEASE_A: Final = Release(
    "1.19.4", "gitea/gitea@sha256:bca3994c102089aeb8d6bacd30d2b4f7056853fe9fb8d709bc81a103079b4f60"
)
RELEASE_B: Final = Release(
    "1.22.6", "gitea/gitea@sha256:538658de667c5d098a274f2f63aa6ec891d88f670cdd5282cf27221ba747dda4"
)
START_URL_INPUT: Final = "start_url"
USER_NAME_INPUT: Final = "user_name"
PASSWORD_SECRET: Final = "gitea_password"  # noqa: S105 - the name of a secret, not a credential
DIRECTORY: Final = Path(__file__).resolve().parent
WORKFLOW_PATH: Final = DIRECTORY / "workflow.yaml"
ISSUE_TITLE: Final = "Benchmark issue"
SIGN_IN: Final = re.compile("sign in", re.IGNORECASE)
CREATE_ISSUE: Final = re.compile("create issue", re.IGNORECASE)


def start_url(base_url: str) -> str:
    """Where the task starts: the sign-in page."""
    return f"{base_url}user/login"


def inputs_for(base_url: str) -> Mapping[str, str]:
    """The workflow's inputs for an instance reachable at ``base_url``."""
    return {START_URL_INPUT: start_url(base_url), USER_NAME_INPUT: ADMIN_USER}


def labels_path(release: Release) -> Path:
    """Where a release's labels live."""
    return DIRECTORY / f"labels-{release.label}.json"


def approval_path(release: Release) -> Path:
    """Where the approval that freezes a release's labels lives."""
    return DIRECTORY / f"labels-{release.label}.approval.json"


def load_pair_labels(release: Release) -> tuple[PairLabels, LabelApproval | None]:
    """A release's labels and its approval, if a person has given one."""
    labels = load_labels(labels_path(release))
    approval = approval_path(release)
    return labels, load_approval(approval) if approval.exists() else None


def plan(
    systems: tuple[str, ...],
    *,
    max_bytes: int,
    release: Release = RELEASE_B,
    notes: tuple[str, ...] = (),
) -> RealAppPlan:
    """Everything needed to score a release: the recorded workflow, the labels, and the approval."""
    workflow = WorkflowYamlCodec(max_bytes=max_bytes).decode(
        WORKFLOW_PATH.read_bytes(), source=str(WORKFLOW_PATH)
    )
    labels, approval = load_pair_labels(release)
    return RealAppPlan(
        pair=PAIR,
        recorded_on=RELEASE_A,
        release=release,
        workflow=workflow,
        workflow_path=WORKFLOW_PATH,
        labels=labels,
        approval=approval,
        systems=systems,
        inputs_for=inputs_for,
        secrets={PASSWORD_SECRET: ADMIN_PASSWORD},
        notes=notes,
    )


async def person(user: ScriptedPerson) -> None:
    """The task: sign in, open the repository and its issues, and create one from the form."""
    page = user.page
    # A fill becomes a step when the field is committed, which the next action does, so each wait
    # counts the steps that exist by then, not the actions performed.
    await page.fill("#user_name", ADMIN_USER)
    await page.fill("#password", ADMIN_PASSWORD)
    await user.steps(2)
    await page.get_by_role("button", name=SIGN_IN).first.click()
    await user.steps(4)
    await page.locator(f'a[href="/{ADMIN_USER}/{REPOSITORY}"]').first.click()
    await user.steps(5)
    await page.locator(f'a[href="/{ADMIN_USER}/{REPOSITORY}/issues"]').first.click()
    await user.steps(6)
    await page.locator(f'a[href="/{ADMIN_USER}/{REPOSITORY}/issues/new"]').first.click()
    await user.steps(7)
    await page.fill("#issue_title", ISSUE_TITLE)
    await page.get_by_role("button", name=CREATE_ISSUE).first.click()
    await user.steps(9)
