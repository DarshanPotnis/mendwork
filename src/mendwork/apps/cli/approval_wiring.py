"""Composition for approvals: Settings and adapters assembled into an ApprovalDesk and a Resumer."""

from collections.abc import Mapping
from typing import Final

from mendwork.adapters.artifacts_local.records import LocalRunRecords
from mendwork.adapters.artifacts_local.store import LocalArtifactStore
from mendwork.adapters.audit_fs.log import FileAuditLog
from mendwork.adapters.secrets_env.resolver import EnvSecretResolver
from mendwork.adapters.system.clock import SystemClock
from mendwork.adapters.system.randomness import SystemRandomSource
from mendwork.adapters.system.timer import AsyncioTimer
from mendwork.apps.cli.wiring import replay_config
from mendwork.engine.healing.model_rung import ModelRung
from mendwork.engine.patching.patcher import Patcher
from mendwork.engine.ports.browser import BrowserLauncher
from mendwork.engine.ports.events import EventSink
from mendwork.engine.ports.resolver import HostResolver
from mendwork.engine.replay.decisions import ApprovalDesk
from mendwork.engine.replay.resume import Resumer
from mendwork.engine.replay.run_execution import ExecutionPorts
from mendwork.engine.safety.egress import EgressPolicy
from mendwork.engine.safety.secret_registry import RegisteringSecretResolver
from mendwork.engine.safety.secret_scrub import SecretScrubber
from mendwork.settings import Settings

AUDIT_DIRECTORY: Final = "audit"
"""The audit log's directory, inside the artifacts directory."""


def build_desk(
    *, artifacts: LocalArtifactStore, environ: Mapping[str, str], scrubber: SecretScrubber
) -> ApprovalDesk:
    """An ApprovalDesk on the local run records, the file audit log, and environment secrets.

    ``scrubber`` is the process's, so every secret the desk resolves is kept out of its logs.
    """
    return ApprovalDesk(
        records=LocalRunRecords(artifacts),
        audit=FileAuditLog(artifacts.root / AUDIT_DIRECTORY),
        artifacts=artifacts,
        secrets=RegisteringSecretResolver(EnvSecretResolver(environ), scrubber),
        clock=SystemClock(),
        scrubber=scrubber,
    )


def build_resumer(
    settings: Settings,
    *,
    launcher: BrowserLauncher,
    artifacts: LocalArtifactStore,
    events: EventSink,
    environ: Mapping[str, str],
    egress: EgressPolicy,
    resolver: HostResolver,
    scrubber: SecretScrubber,
    model: ModelRung | None = None,
    patcher: Patcher | None = None,
) -> Resumer:
    """A Resumer that executes approved runs exactly as ``mendwork run`` executes new ones.

    ``patcher`` saves the heals a resumed run verifies, the approved one included (ADR 0013).
    """
    ports = ExecutionPorts(
        launcher=launcher,
        artifacts=artifacts,
        secrets=RegisteringSecretResolver(EnvSecretResolver(environ), scrubber),
        clock=SystemClock(),
        timer=AsyncioTimer(),
        randomness=SystemRandomSource(),
        config=replay_config(settings),
        egress=egress,
    )
    return Resumer(ports=ports, events=events, resolver=resolver, model=model, patcher=patcher)
