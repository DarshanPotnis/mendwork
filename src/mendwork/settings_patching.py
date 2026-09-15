"""Patching settings: where workflow versions live, and when verified heals become versions.

A base of ``Settings``, kept in its own module like the model and egress settings; every field is
still a ``MENDWORK_`` variable of the one Settings object. See ADR 0013.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

from mendwork.engine.domain.enums import PromotionPolicy

_MIB = 1024 * 1024


class PatchingSettings(BaseSettings):
    """The workflow store, the promotion policy, and the run report's size."""

    # Workflow versions and pending patches: <dir>/<workflow_id>/v0001.yaml and <dir>/.pending/.
    # A file given to `mendwork run` is matched to its stored versions by content and never written.
    workflow_store_dir: Path = Path("workflow-store")
    # immediate: a verified heal from a succeeded run becomes a version at once. after_n_successes:
    # it waits until that many succeeded runs verified it, and is tried first until then.
    patch_promotion: PromotionPolicy = PromotionPolicy.IMMEDIATE
    # Policy, not capability. 1 would be immediate; 3 means two more succeeded runs, on fresh page
    # loads, found the healed element and passed its checkpoints.
    patch_promotion_successes: int = Field(default=3, ge=2, le=100)
    # A retry means another process published a version of the same workflow while this one was
    # being published; five concurrent publishers of one workflow is beyond what the CLI runs.
    patch_publish_attempts: int = Field(default=5, ge=1, le=20)
    # PNG bytes a run report embeds. Measured portal screenshots are 47-67 KB, so 8 MiB embeds at
    # least 125 of them; an image past the budget is named in the report instead of shown.
    report_screenshots_max_bytes: int = Field(default=8 * _MIB, ge=0, le=256 * _MIB)
