"""Composition for patching: the workflow store, pending patches, and the Patcher (ADR 0013)."""

from pathlib import Path

from mendwork.adapters.storage_fs.pending_patches import FilePendingPatches
from mendwork.adapters.storage_fs.workflow_store import FileWorkflowStore
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.workflow_yaml.codec import WorkflowYamlCodec
from mendwork.engine.patching.config import PatchingConfig
from mendwork.engine.patching.patcher import Patcher
from mendwork.settings import Settings


def store_directory(settings: Settings, override: Path | None) -> Path:
    """The workflow store's directory: ``--store-dir`` when given, otherwise Settings."""
    return override if override is not None else settings.workflow_store_dir


def workflow_store(settings: Settings, root: Path) -> FileWorkflowStore:
    """The file workflow store under ``root``, reading files no larger than a workflow may be."""
    return FileWorkflowStore(root, WorkflowYamlCodec(max_bytes=settings.workflow_max_bytes))


def pending_patches(root: Path) -> FilePendingPatches:
    """The pending patches kept beside the workflow store under ``root``."""
    return FilePendingPatches(root)


def patching_config(settings: Settings) -> PatchingConfig:
    """When verified heals become versions, taken from Settings."""
    return PatchingConfig(
        promotion=settings.patch_promotion,
        successes_required=settings.patch_promotion_successes,
        publish_attempts=settings.patch_publish_attempts,
    )


def build_patcher(settings: Settings, root: Path) -> Patcher:
    """A Patcher on the file store and pending patches under ``root``, on the real clock."""
    return Patcher(
        store=workflow_store(settings, root),
        pending=pending_patches(root),
        clock=SystemClock(),
        config=patching_config(settings),
    )
